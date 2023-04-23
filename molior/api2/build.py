from ..app import app, logger
from ..model.build import Build
from ..molior.queues import enqueue_aptly
from ..tools import OKResponse, ErrorResponse


@app.http_delete("/api2/build/{build_id}")
@app.authenticated
async def delete_build(request):
    """
    Delete build from the database.

    ---
    description: Delete build from database.
    tags:
        - Builds
    parameters:
        - name: build_id
          description: id of the build to delete
          in: path
          required: true
          type: integer
    produces:
        - text/json
    """
    build_id = request.match_info["build_id"]
    try:
        build_id = int(build_id)
    except (ValueError, TypeError):
        return ErrorResponse(400, "Incorrect value for build_id")

    db = request.cirrina.db_session
    build = db.query(Build).filter(Build.id == build_id).first()
    if not build:
        logger.error("build %d not found" % build_id)
        return ErrorResponse(404, "Build not found")

    topbuild = None
    builds = []
    if build.buildtype == "build":
        topbuild = build
        builds.append(topbuild)
        for b in topbuild.children:
            builds.append(b)
            builds.extend(iter(b.children))
    elif build.buildtype == "deb":
        topbuild = build.parent.parent
        builds.extend([build.parent, build.parent.parent])
        builds.extend(iter(build.parent.children))
    elif build.buildtype == "source":
        topbuild = build.parent
        builds.extend([build, build.parent])
        builds.extend(iter(build.children))
    if not topbuild:
        return ErrorResponse(400, f"Build of type {build.buildtype} cannot be deleted")

    for srcbuild in topbuild.children:
        for debbuild in srcbuild.children:
            if debbuild.projectversion and debbuild.projectversion.is_locked:
                return ErrorResponse(400, "Build from locked projectversion cannot be deleted")

            if debbuild.buildstate in [
                "scheduled",
                "building",
                "needs_publish",
                "publishing",
            ]:
                return ErrorResponse(
                    400,
                    f"Build in state {debbuild.buildstate} cannot be deleted",
                )

    for b in builds:
        b.is_deleted = True
    db.commit()

    await enqueue_aptly({"delete_build": [topbuild.id]})
    return OKResponse("Build is being deleted")


@app.http_post("/api2/build/{build_id}/abort")
@app.authenticated
async def abort_build(request):
    """
    Abort a running build

    ---
    description: Delete a build from database.
    tags:
        - Builds
    consumes:
        - application/x-www-form-urlencoded
    parameters:
        - name: build_id
          in: path
          required: true
          type: integer
    produces:
        - text/json
    responses:
        "200":
            description: successful
        "500":
            description: internal server error
    """
    build_id = request.match_info["build_id"]
    try:
        build_id = int(build_id)
    except (ValueError, TypeError):
        return ErrorResponse(400, "Incorrect value for build_id")

    build = request.cirrina.db_session.query(Build).filter(Build.id == build_id).first()
    if not build:
        logger.error("build %d not found" % build_id)
        return ErrorResponse(404, "Build not found")

    topbuild = None
    srcbuild = None
    if build.buildtype == "deb":
        topbuild = build.parent.parent
        srcbuild = build.parent
    elif build.buildtype == "source":
        topbuild = build.parent
        srcbuild = build
    elif build.buildtype == "build":
        topbuild = build
        srcbuild = build.children[0]
    else:
        return ErrorResponse(404, f"Build type '{build.buildtype}' cannot be aborted")

    if srcbuild.sourcerepository is None:
        return ErrorResponse(404, "External build uploads cannot be aborted")

    found = any(
        deb.buildstate in ["building", "needs_build"]
        for deb in srcbuild.children
    )
    if not found:
        return ErrorResponse(404, "No running deb builds found")

    logger.info(f"aborting build {topbuild.id}")

    args = {"abort": [topbuild.id]}
    await enqueue_aptly(args)
    return OKResponse("Abort initiated")
