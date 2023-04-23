#!/bin/sh

chown -R postgres:postgres /var/lib/postgresql/*

exec su postgres -c "exec /usr/lib/postgresql/13/bin/postgres -D /var/lib/postgresql/13/main -c config_file=/etc/postgresql/13/main/postgresql.conf"
