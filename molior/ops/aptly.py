import os
import re

from launchy import Launchy
from pathlib import Path
from aiofile import AIOFile

from ..app import logger
from ..tools import strip_epoch_version, db2array
from ..molior.debianrepository import DebianRepository
from ..molior.configuration import Configuration
from ..molior.queues import buildlog, buildlogtitle

from ..model.database import Session
from ..model.build import Build
from ..model.buildtask import BuildTask
from ..model.projectversion import ProjectVersion
from ..model.debianpackage import Debianpackage


def get_debchanges_filename(sourcepath, sourcename, version, arch="source"):
    v = strip_epoch_version(version)
    return f"{sourcepath}/{sourcename}_{v}_{arch}.changes"


async def debchanges_get_files(sourcepath, sourcename, version, arch="source"):
    changes_file = get_debchanges_filename(sourcepath, sourcename, version, arch)
    files = []
    try:
        async with AIOFile(changes_file, "rb") as f:
            data = await f.read()
            file_tag = False
            for line in str(data, 'utf-8').split('\n'):
                line = line.rstrip()
                if file_tag:
                    if not line.startswith(" "):
                        break
                    line = line.lstrip()
                    parts = line.split(" ")
                    files.append(parts[4])
                elif line == "Files:":
                    file_tag = True
    except Exception as exc:
        logger.exception(exc)
    return files


async def DebSrcPublish(build_id, repo_id, sourcename, version, projectversions, is_ci):
    """
    Publishes given src_files/src package to given
    projectversion debian repo.

    Args:
        build: source package build

    Returns:
        bool: True if successful, otherwise False.
    """
    buildtype = "source"

    await buildlog(build_id, "\n")
    await buildlogtitle(build_id, "Publishing")

    if repo_id:
        sourcepath = Path(Configuration().working_dir) / "repositories" / str(repo_id)
    else:
        sourcepath = Path(Configuration().working_dir) / "buildout" / str(build_id)

    srcfiles = []
    if Path(get_debchanges_filename(sourcepath, sourcename, version, "source")).exists():
        # check exists
        srcfiles = await debchanges_get_files(sourcepath, sourcename, version)
    else:  # source build without changes file, i.e. external build upload
        for sourcefile in sourcepath.glob("*.*"):
            filename = sourcefile.name
            if filename != "build.log":
                srcfiles.append(filename)

    if not srcfiles:
        logger.error("DebSrcPublish: no source files found")
        return False

    await buildlog(build_id, "I: uploading files to aptly\n")
    publish_files = []
    for f in srcfiles:
        await buildlog(build_id, " - %s\n" % f)
        publish_files.append(f"{sourcepath}/{f}")

    add_files(build_id, buildtype, version, srcfiles)

    ret = False
    for projectversion_id in projectversions:
        fullname = None
        with Session() as session:
            if (
                projectversion := session.query(ProjectVersion)
                .filter(ProjectVersion.id == projectversion_id)
                .first()
            ):
                fullname = projectversion.fullname
                basemirror_name = projectversion.basemirror.project.name
                basemirror_version = projectversion.basemirror.name
                project_name = projectversion.project.name
                project_version = projectversion.name
                archs = db2array(projectversion.mirror_architectures)

        if not fullname:
            logger.error(f"publisher: error finding projectversion {projectversion_id}")
            await buildlog(
                build_id,
                f"E: error finding projectversion {projectversion_id}\n",
            )
            continue

        await buildlog(build_id, "I: publishing for project %s\n" % fullname)

        debian_repo = DebianRepository(basemirror_name, basemirror_version, project_name, project_version, archs)
        try:
            ret = await debian_repo.add_packages(publish_files, ci_build=is_ci)
        except Exception as exc:
            await buildlog(build_id, "E: error adding files\n")
            logger.exception(exc)

    await buildlog(build_id, "\n")

    if ret:  # only delete if published, allow republish
        files2delete = publish_files
        changes_file = get_debchanges_filename(sourcepath, sourcename, version, "source")
        if Path(changes_file).exists():
            files2delete.append(changes_file)
        for f in files2delete:
            logger.debug("publisher: removing %s", f)
            try:
                os.remove(f)
            except Exception as exc:
                logger.exception(exc)

    return ret


async def publish_packages(build_id, buildtype, sourcename, version, architecture, is_ci,
                           basemirror_name, basemirror_version, project_name, project_version, archs, out_path):
    """
    Publishes given packages to given
    publish point.

    Args:
        build (Build): The build model.
        out_path (Path): The build output path.

    Returns:
        bool: True if successful, otherwise False.
    """

    outfiles = await debchanges_get_files(out_path, sourcename, version, architecture)
    add_files(build_id, buildtype, version, outfiles)
    # FIXME: commit

    files2upload = []
    for f in outfiles:
        logger.debug("publisher: adding %s", f)
        files2upload.append("{}/{}".format(out_path, f))

    count_files = len(files2upload)
    if count_files == 0:
        logger.error("publisher: build %d: no files to upload", build_id)
        await buildlog(build_id, "E: no debian packages found to upload\n")
        return False

    # FIXME: check on startup
    key = Configuration().debsign_gpg_email
    if not key:
        logger.error("Signing key not defined in configuration")
        await buildlog(build_id, "E: no signinig key defined in configuration\n")
        return False

    await buildlog(build_id, "Signing packages:\n")

    async def outh(line):
        if len(line.strip()) != 0:
            await buildlog(build_id, "%s\n" % re.sub(r"^ *", " - ", line))

    v = strip_epoch_version(version)
    changes_file = "{}_{}_{}.changes".format(sourcename, v, architecture)

    cmd = "debsign -pgpg1 -k{} {}".format(key, changes_file)
    process = Launchy(cmd, outh, outh, cwd=str(out_path))
    await process.launch()
    ret = await process.wait()
    if ret != 0:
        logger.error("debsign failed")
        return False

    logger.debug("publisher: uploading %d file%s", count_files, "" if count_files == 1 else "s")

    debian_repo = DebianRepository(basemirror_name, basemirror_version, project_name, project_version, archs)
    ret = False
    try:
        ret = await debian_repo.add_packages(files2upload, ci_build=is_ci)
    except Exception as exc:
        await buildlog(build_id, "E: error uploading files to repository\n")
        logger.exception(exc)

    files2delete = files2upload
    files2delete.append("{}/{}".format(out_path, changes_file))
    for f in files2delete:
        logger.info("publisher: removing %s", f)
        try:
            os.remove(f)
        except Exception as exc:
            logger.exception(exc)

    return ret


async def DebPublish(build_id, buildtype, sourcename, version, architecture, is_ci,
                     basemirror_name, basemirror_version, project_name, project_version, archs):
    """
    Publishes given src_files/src package to given
    projectversion debian repo.

    Args:
        projectversion_id (int): The projectversion's id.
        src_files (list): List of file paths to the src files.

    Returns:
        bool: True if successful, otherwise False.
    """

    out_path = Path(Configuration().working_dir) / "buildout" / str(build_id)
    await buildlogtitle(build_id, "Publishing", no_header_newline=False)

    try:
        if not await publish_packages(build_id, buildtype, sourcename, version, architecture, is_ci,
                                      basemirror_name, basemirror_version, project_name, project_version, archs, out_path):
            logger.error("publisher: error publishing build %d" % build_id)
            return False
    except Exception as exc:
        logger.error("publisher: error publishing build %d" % build_id)
        logger.exception(exc)
        return False
    finally:
        with Session() as session:
            if (
                buildtask := session.query(BuildTask)
                .filter(BuildTask.build_id == build_id)
                .first()
            ):
                session.delete(buildtask)
                session.commit()
    return True


def add_files(build_id, buildtype, version, files):
    packages = {}
    for f in files:
        name = ""
        version = ""
        arch = ""
        ext = ""
        suffix = ""

        p = f.split("_")

        if buildtype == "deb":
            if len(p) != 3:
                logger.error(f"build: unknown debian package file: {f}")
                continue
            name, version, suffix = p
            s = suffix.split(".", 2)
            if len(s) != 2:
                logger.error(f"build: cannot add file: {f}")
                continue
            arch, ext = s
            suffix = arch
            if ext != "deb":
                continue

        elif buildtype == "source":
            if len(p) == 3:  # $pkg_$ver_source.buildinfo
                continue
            if len(p) != 2:
                logger.error(f"build: unknown source package file: {f}")
                continue

            name, suffix = p
            suffix = suffix.replace(version, "")
            suffix = suffix[1:]  # remove dot
            if suffix.endswith("dsc") or suffix.endswith("source.buildinfo"):
                continue

        key = f"{name}_{suffix}:"
        if key not in packages.keys():
            packages[key] = (name, suffix)

    with Session() as session:
        build = session.query(Build).filter(Build.id == build_id).first()
        if not build:
            logger.error("clone: build %d not found", build_id)
            return
        for package in packages:
            name, suffix = packages[package]
            pkg = session.query(Debianpackage).filter_by(name=name, suffix=suffix).first()
            if not pkg:
                pkg = Debianpackage(name=name, suffix=suffix)
            if pkg not in build.debianpackages:
                build.debianpackages.append(pkg)
        session.commit()
