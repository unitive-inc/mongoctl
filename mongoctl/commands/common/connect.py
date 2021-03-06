__author__ = 'abdul'

import mongoctl.repository as repository

from mongoctl.commands.command_utils import (
    extract_mongo_exe_options, get_mongo_executable, options_to_command_args,
    VersionPreference
)
from mongoctl.mongoctl_logging import log_info, log_error
from mongoctl.mongo_uri_tools import is_mongo_uri, parse_mongo_uri

from mongoctl.errors import MongoctlException

from mongoctl.utils import call_command
from mongoctl.objects.server import Server

from mongoctl.objects.mongod import MongodServer

###############################################################################
# CONSTS
###############################################################################
SUPPORTED_MONGO_SHELL_OPTIONS = [
    "shell",
    "norc",
    "quiet",
    "eval",
    "verbose",
    "ipv6",
    "port",
    "authenticationDatabase",
    "authenticationMechanism",
    "ssl",
    "sslCAFile",
    ]

###############################################################################
# connect command
###############################################################################
def connect_command(parsed_options):
    shell_options = extract_mongo_shell_options(parsed_options)
    open_mongo_shell_to(parsed_options.dbAddress,
                        username=parsed_options.username,
                        password=parsed_options.password,
                        shell_options=shell_options,
                        js_files=parsed_options.jsFiles)


###############################################################################
def extract_mongo_shell_options(parsed_args):
    return extract_mongo_exe_options(parsed_args,
                                     SUPPORTED_MONGO_SHELL_OPTIONS)


###############################################################################
# open_mongo_shell_to
###############################################################################
def open_mongo_shell_to(db_address,
                        username=None,
                        password=None,
                        shell_options=None,
                        js_files=None):
    if is_mongo_uri(db_address):
        open_mongo_shell_to_uri(db_address, username, password,
                                shell_options, js_files)
        return

    # db_address is an id string
    id_path = db_address.split("/")
    id = id_path[0]
    database = id_path[1] if len(id_path) == 2 else None

    server = repository.lookup_server(id)
    if server:
        open_mongo_shell_to_server(server, database, username, password,
                                   shell_options, js_files)
        return

    # Maybe cluster?
    cluster = repository.lookup_cluster(id)
    if cluster:
        open_mongo_shell_to_cluster(cluster, database, username, password,
                                    shell_options, js_files)
        return
        # Unknown destination
    raise MongoctlException("Unknown db address '%s'" % db_address)

###############################################################################
def open_mongo_shell_to_server(server,
                               database=None,
                               username=None,
                               password=None,
                               shell_options=None,
                               js_files=None):
    repository.validate_server(server)

    if not database:
        if isinstance(server, MongodServer) and server.is_arbiter_server():
            database = "local"
        else:
            database = "admin"

    login_database = shell_options["authenticationDatabase"] \
        if "authenticationDatabase" in shell_options else database

    if username or server.needs_to_auth(login_database):
        # authenticate and grab a working username/password
        username, password = server.get_working_login(login_database, username,
                                                      password)

    do_open_mongo_shell_to(server.get_connection_address(),
                           database=database,
                           username=username,
                           password=password,
                           server_version=server.get_mongo_version_info(),
                           shell_options=shell_options,
                           js_files=js_files,
                           ssl=server.use_ssl_client())

###############################################################################
def open_mongo_shell_to_cluster(cluster,
                                database=None,
                                username=None,
                                password=None,
                                shell_options=None,
                                js_files=None):

    log_info("Locating default server for cluster '%s'..." % cluster.id)
    default_server = cluster.get_default_server()
    if default_server:
        log_info("Connecting to server '%s'" % default_server.id)
        open_mongo_shell_to_server(default_server,
                                   database=database,
                                   username=username,
                                   password=password,
                                   shell_options=shell_options,
                                   js_files=js_files)
    else:
        log_error("No default server found for cluster '%s'" %
                  cluster.id)

###############################################################################
def open_mongo_shell_to_uri(uri,
                            username=None,
                            password=None,
                            shell_options=None,
                            js_files=None):

    uri_wrapper = parse_mongo_uri(uri)
    database = uri_wrapper.database
    username = username if username else uri_wrapper.username
    password = password if password else uri_wrapper.password

    if "authsource" in uri_wrapper.options and "authenticationDatabase" not in shell_options:
        shell_options["authenticationDatabase"] = uri_wrapper.options["authsource"]

    if "authmechanism" in uri_wrapper.options and "authenticationMechanism" not in shell_options:
        shell_options["authenticationMechanism"] = uri_wrapper.options["authmechanism"]


    server_or_cluster = repository.build_server_or_cluster_from_uri(uri)

    if isinstance(server_or_cluster, Server):
        open_mongo_shell_to_server(server_or_cluster,
                                   database=database,
                                   username=username,
                                   password=password,
                                   shell_options=shell_options,
                                   js_files=js_files)
    else:
        open_mongo_shell_to_cluster(server_or_cluster,
                                    database=database,
                                    username=username,
                                    password=password,
                                    shell_options=shell_options,
                                    js_files=js_files)

###############################################################################
def do_open_mongo_shell_to(address,
                           database=None,
                           username=None,
                           password=None,
                           server_version=None,
                           shell_options=None,
                           js_files=None,
                           ssl=False):

    # default database to admin
    database = database if database else "admin"

    shell_options = shell_options or {}
    js_files = js_files or []

    # override port if specified in --port
    if "port" in shell_options:
        address = "%s:%s" % (address.split(":")[0], shell_options["port"])
        # remove port from options since passing address + port is disallowed in mongo
        del shell_options["port"]
    connect_cmd = [get_mongo_shell_executable(server_version),
                   "%s/%s" % (address, database)]

    if username:
        connect_cmd.extend(["-u",username, "-p"])
        if password:
            connect_cmd.extend([password])

    # append shell options
    if shell_options:
        connect_cmd.extend(options_to_command_args(shell_options))

    # append js files
    if js_files:
        connect_cmd.extend(js_files)

    # ssl options
    if ssl and "--ssl" not in connect_cmd:
        connect_cmd.append("--ssl")

    cmd_display = connect_cmd[:]
    # mask user/password
    if username:
        cmd_display[cmd_display.index("-u") + 1] =  "****"
        if password:
            cmd_display[cmd_display.index("-p") + 1] =  "****"

    log_info("Executing command: \n%s" % " ".join(cmd_display))
    call_command(connect_cmd, bubble_exit_code=True)


###############################################################################
def get_mongo_shell_executable(server_version):
    shell_exe = get_mongo_executable(server_version,
                                     'mongo',
                                     version_check_pref=VersionPreference.EXACT_OR_MINOR)
    return shell_exe.path
