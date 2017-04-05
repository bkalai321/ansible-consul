#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# (c) 2015, Brian Coca <bcoca@ansible.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>

ANSIBLE_METADATA = {'metadata_version': '1.0',
                    'status': ['stableinterface'],
                    'supported_by': 'community'}


# This is a modification of @bcoca's `svc` module

DOCUMENTATION = '''
---
module: runit
author: "James Sumners (@jsumners)"
version_added: "2.3"
short_description:  Manage runit services.
description:
    - Controls runit services on remote hosts using the sv utility.
options:
    name:
        required: true
        description:
            - Name of the service to manage.
    state:
        required: false
        choices: [ started, stopped, restarted, killed, reloaded, once ]
        description:
            - C(started)/C(stopped) are idempotent actions that will not run
              commands unless necessary.  C(restarted) will always bounce the
              service (sv restart) and C(killed) will always bounce the service (sv force-stop).
              C(reloaded) will send a HUP (sv reload).
              C(once) will run a normally downed sv once (sv once), not really
              an idempotent operation.
    enabled:
        required: false
        choices: [ "yes", "no" ]
        description:
            - Wheater the service is enabled or not, if disabled it also implies stopped.
    service_dir:
        required: false
        default: /var/service
        description:
            - directory runsv watches for services
    service_src:
        required: false
        default: /etc/sv
        description:
            - directory where services are defined, the source of symlinks to service_dir.
'''

EXAMPLES = '''
# Example action to start sv dnscache, if not running
 - sv:
    name: dnscache
    state: started

# Example action to stop sv dnscache, if running
 - sv:
    name: dnscache
    state: stopped

# Example action to kill sv dnscache, in all cases
 - sv:
    name: dnscache
    state: killed

# Example action to restart sv dnscache, in all cases
 - sv:
    name: dnscache
    state: restarted

# Example action to reload sv dnscache, in all cases
 - sv:
    name: dnscache
    state: reloaded

# Example using alt sv directory location
 - sv:
    name: dnscache
    state: reloaded
    service_dir: /run/service
'''

import platform
import shlex
from ansible.module_utils.pycompat24 import get_exception
from ansible.module_utils.basic import *

def _load_dist_subclass(cls, *args, **kwargs):
    '''
    Used for derivative implementations
    '''
    subclass = None

    distro = kwargs['module'].params['distro']

    # get the most specific superclass for this platform
    if distro is not None:
        for sc in cls.__subclasses__():
            if sc.distro is not None and sc.distro == distro:
                subclass = sc
    if subclass is None:
        subclass = cls

    return super(cls, subclass).__new__(subclass)

class Sv(object):
    """
    Main class that handles daemontools, can be subclassed and overridden in case
    we want to use a 'derivative' like encore, s6, etc
    """


    #def __new__(cls, *args, **kwargs):
    #    return _load_dist_subclass(cls, args, kwargs)

    def __init__(self, module):
        self.extra_paths = [ ]
        self.report_vars = ['state', 'enabled', 'svc_full', 'src_full', 'pid', 'duration', 'full_state']

        self.module         = module

        self.name           = module.params['name']
        self.service_dir    = module.params['service_dir']
        self.service_src    = module.params['service_src']
        self.enabled        = None
        self.full_state     = None
        self.state          = None
        self.pid            = None
        self.duration       = None
        self.wants_down     = False

        self.svc_cmd        = module.get_bin_path('s6-svc', opt_dirs=self.extra_paths)
        self.svstat_cmd     = module.get_bin_path('s6-svstat', opt_dirs=self.extra_paths)
        self.svc_full       = '/'.join([ self.service_dir, self.name ])
        self.src_full       = '/'.join([ self.service_src, self.name ])

        self.enabled = os.path.lexists(self.svc_full)
        if self.enabled:
            self.get_status()
        else:
            self.state = 'stopped'


    def enable(self):
        if os.path.exists(self.src_full):
            try:
                os.symlink(self.src_full, self.svc_full)
            except OSError:
                e = get_exception()
                self.module.fail_json(path=self.src_full, msg='Error while linking: %s' % str(e))
        else:
            self.module.fail_json(msg="Could not find source for service to enable (%s)." % self.src_full)

    def disable(self):
        self.execute_command([self.svc_cmd, 'force-stop',self.src_full])
        try:
            os.unlink(self.svc_full)
        except OSError:
            e = get_exception()
            self.module.fail_json(path=self.svc_full, msg='Error while unlinking: %s' % str(e))

    def check_return(self, action, (rc, out, err)):
        if rc != 0:
            self.module.fail_json(msg="s6 '{}' failed.".format(action), error=err)
        return (rc, out, err)


    def get_status(self):

        (rc, out, err) = self.execute_command([self.svstat_cmd, self.svc_full])

        if err is not None and err:
            self.full_state = self.state = err
        else:
            self.full_state = out

            m = re.search('\(pid (\d+)\)', out)
            if m:
                self.pid = m.group(1)

            m = re.search(' (\d+)s', out)
            if m:
                self.duration = m.group(1)

            if re.search('want down', out):
                self.state = True

            if re.search('^up', out):
                self.state = 'started'
            elif re.search('^down', out):
                self.state = 'stopped'
            else:
                self.state = 'unknown'
                return

    def started(self):
        return self.check_return("started", self.start())

    def start(self):
        return self.execute_command([self.svc_cmd, '-u', self.svc_full])

    def stopped(self):
        return self.check_return("stopped", self.stop())

    def stop(self):
        return self.execute_command([self.svc_cmd, '-d', self.svc_full])

    def once(self):
        return self.check_return("started once", self.execute_command([self.svc_cmd, '-O', self.svc_full]))

    # def reloaded(self):
    #     return self.reload()

    # def reload(self):
    #     return self.execute_command([self.svc_cmd, 'reload', self.svc_full])

    def restarted(self):
        if self.state == "started":
            self.killed()
        elif self.state == 'unknown':
            self.module.fail_json(msg="Service is in unknown state. duno what to do")
        # Lets start (dep)
        if self.wants_down:
            return self.once()
        else:
            return self.start()

    def restart(self):
        return self.execute_command([self.svc_cmd, 'restart', self.svc_full])

    def killed(self):
        return self.check_return("killed", self.kill())

    def kill(self):
        return self.execute_command([self.svc_cmd, '-k', self.svc_full])

    def execute_command(self, cmd):
        try:
            (rc, out, err) = self.module.run_command(' '.join(cmd))
        except Exception:
            e = get_exception()
            self.module.fail_json(msg="failed to execute: %s" % str(e))
        return (rc, out, err)

    def report(self):
        self.get_status()
        states = {}
        for k in self.report_vars:
            states[k] = self.__dict__[k]
        return states

# ===========================================
# Main control flow

def main():
    module = AnsibleModule(
        argument_spec = dict(
            name = dict(required=True),
            state = dict(choices=['started', 'stopped', 'restarted', 'killed', 'once']),
            enabled = dict(required=False, type='bool'),
            dist = dict(required=False, default='runit'),
            service_dir = dict(required=False, default='/var/service'),
            service_src = dict(required=False, default='/etc/sv'),
        ),
        supports_check_mode=True,
    )

    module.run_command_environ_update = dict(LANG='C', LC_ALL='C', LC_MESSAGES='C', LC_CTYPE='C')

    state = module.params['state']
    enabled = module.params['enabled']

    sv = Sv(module)
    changed = False
    orig_state = sv.report()

    if enabled is not None and enabled != sv.enabled:
        changed = True
        if not module.check_mode:
            try:
                if enabled:
                    sv.enable()
                else:
                    sv.disable()
            except (OSError, IOError):
                e = get_exception()
                module.fail_json(msg="Could not change service link: %s" % str(e))

    if state is not None and state != sv.state:
        changed = True
        if not module.check_mode:
            getattr(sv,state)()

    module.exit_json(changed=changed, sv=sv.report())




if __name__ == '__main__':
    main()

'''

-o : once. Equivalent to "-uO".
-d : down. If the supervised process is up, send it a SIGTERM and a SIGCONT. Do not restart it.
-u : up. If the supervised process is down, start it. Automatically restart it when it dies.
-x : exit. When the service is asked to be down and the supervised process dies, s6-supervise will exit too. This command should normally never be used on a working system.
-X : close fds and exit. Like -x, but s6-supervise will immediately close its stdin, stdout and stderr. This is useful when s6-supervise has descriptors open to the service it is supervising and the service is waiting for them to close before exiting. Note that if this option is used, the last execution of the service's finish script will be run with stdin, stdout and stderr redirected to /dev/null.
-O : Once at most. Do not restart the supervised process when it dies. If it is down when the command is received, do not even start it.
-T timeout : if the -wstate option has been given, -T specifies a timeout (in milliseconds) after which s6-svc will exit 1 with an error message if the service still hasn't reached the desired state. By default, the timeout is 0, which means that s6-svc will block indefinitely.
-wd : s6-svc will not exit until the service is down, i.e. until the run process has died.
-wD : s6-svc will not exit until the service is down and ready to be brought up, i.e. a possible finish script has exited.
-wu : s6-svc will not exit until the service is up, i.e. there is a process running the run executable.
-wU : s6-svc will not exit until the service is up and ready as notified by the daemon itself. If the service directory does not contain a notification-fd file to tell s6-supervise to accept readiness notification, s6-svc will print a warning and act as if the -wu option had been given instead.
-wr : s6-svc will not exit until the service has been started or restarted.
-wR : s6-svc will not exit until the service has been started or restarted and has notified readiness.
'''

'''
wantedup: print true if s6-supervise is currently instructed to (re)start the service when it is down, and false if s6-supervise is currently instructed to leave the service alone.
normallyup: print true if the service is supposed to start when s6-supervise starts (i.e. no ./down file), and false if it is not (i.e. there is a ./down file).
paused: print true if the service is paused (i.e. the process is currently stopped) and false if it is not. It is a rare flag, you shouldn't normally need to use this option.

'''