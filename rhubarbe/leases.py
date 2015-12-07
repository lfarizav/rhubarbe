import asyncio
import aiohttp
import os
import time, calendar
from datetime import datetime
import json

from rhubarbe.logger import logger
from rhubarbe.config import Config

debug = False
#debug = True

# Nov 2015
# what we get from omf_sfa is essentially something like this
#root@faraday /tmp/asyncio # curl -k https://localhost:12346/resources/leases
# {
#  "resource_response": {
#    "resources": [
#      {
#        "urn": "urn:publicid:IDN+omf:r2lab+lease+ee3614fb-74d2-4097-99c5-fe0b988f2f2d",
#        "uuid": "ee3614fb-74d2-4097-99c5-fe0b988f2f2d",
#        "resource_type": "lease",
#        "valid_from": "2015-11-26T10:30:00Z",
#        "valid_until": "2015-11-26T11:30:00Z",
#        "status": "accepted",
#        "client_id": "b089e80a-3b0a-4580-86ba-aacff6e4043e",
#        "components": [
#          {
#            "name": "r2lab",
#            "urn": "urn:publicid:IDN+omf:r2lab+node+r2lab",
#            "uuid": "11fbdd9e-067f-4ee9-bd98-3b12d63fe189",
#            "resource_type": "node",
#            "domain": "omf:r2lab",
#            "available": true,
#            "status": "unknown",
#            "exclusive": true
#          }
#        ],
#        "account": {
#          "name": "onelab.inria.foo1",
#          "urn": "urn:publicid:IDN+onelab:inria+slice+foo1",
#          "uuid": "6a49945c-1a17-407e-b334-dca4b9b40373",
#          "resource_type": "account",
#          "created_at": "2015-11-26T10:22:26Z",
#          "valid_until": "2015-12-08T15:13:52Z"
#        }
#      }
#    ],
#    "about": "/resources/leases"
#  }
#
# myslice tends to keep leases contiguous, and so does a lot of delete/create
# in this case it seems omf_sfa keeps the lease object but removes the subject from the 'components'
# field. In any case it is quite frequent to see this 'components' being an empty list

class Lease:
    """
    a simple extract from the omf_sfa loghorrea
    """

    wire_timeformat = "%Y-%m-%dT%H:%M:%S%Z"

    def __init__(self, omf_sfa_resource):
        r = omf_sfa_resource
        try:
            self.owner = r['account']['name']
            # this is only for information since there's only one node exposed to SFA
            # so, we take only the first name
            self.subjects = [component['name'] for component in r['components']]
            sfrom = r['valid_from']
            suntil = r['valid_until']
            # turns out that datetime.strptime() does not seem to like
            # the terminal 'Z', so let's do this manually
            if sfrom[-1] == 'Z': sfrom = sfrom[:-1] + 'UTC'
            if suntil[-1] == 'Z': suntil = suntil[:-1] + 'UTC'
            self.ifrom = calendar.timegm(time.strptime(sfrom, self.wire_timeformat))
            self.iuntil = calendar.timegm(time.strptime(suntil, self.wire_timeformat))
            self.broken = False
            self.unique_component_name = Config().value('authorization', 'component_name')

        except Exception as e:
            self.broken = "lease broken b/c of exception {}".format(e)

        if not self.subjects:
            self.broken = "lease has no subject component"    

    def __repr__(self):
        now = time.time()
        if self.iuntil < now:
            time_message = 'expired'
        elif self.ifrom < now:
            time_message = "from now until {}".format(self.human(self.iuntil, False))
        else:
            time_message = 'from {} until {}'.format(
                self.human(self.ifrom, show_timezone=False),
                self.human(self.iuntil, show_date=False))
        # usual case is that self.subjects == [unique_component_name]
        if len(self.subjects) == 1 and self.subjects[0] == self.unique_component_name:
            scope = ""
        else:
            scope = " -> {}".format(" & ".join(self.subjects))
        overall = "{}{} - {}".format(self.owner, scope, time_message)
        if self.broken:
            overall = "<BROKEN {}> ".format(self.broken) + overall
        return overall

    def sort_key(self):
        return self.ifrom

    @staticmethod
    def human(epoch, show_date=True, show_timezone=True):
        human_timeformat_date = "%m-%d @ %H:%M %Z"
        human_timeformat_time_and_zone = "%H:%M %Z"
        human_timeformat_time = "%H:%M"
        format = human_timeformat_date if show_date \
                 else human_timeformat_time_and_zone if show_timezone \
                      else human_timeformat_time
        return time.strftime(format, time.localtime(epoch))

    def is_valid(self, login):
        if debug: print("is_valid with lease {}".format(self), end="")
        if self.broken:
            logger.info("ignoring broken lease {}".format(self))
            if debug: print("is broken")
            return False
        if not self.owner == login:
            logger.info("{} : wrong login {} - owner is {}".format(self, login, self.owner))
            if debug: print("{} is not owner {}".format(login, self.owner))
            return False
        if not self.ifrom <= time.time() <= self.iuntil:
            if debug: print("not the right time")
            logger.info("{} : wrong timerange".format(self))
            return False
        if self.unique_component_name not in self.subjects:
            if debug: print("expected {} among subjects {}"
                            .format(self.unique_component_name, self.subjects))
            logger.info("expected {} among subjects {}"
                        .format(self.unique_component_name, self.subjects))
            return False
        # nothing more to check; the subject name cannot be wrong, there's only
        # one node that one can get a lease on
        if debug: print("fine")
        return self

####################
class Leases:
    # the details of the omf_sfa instance where to look for leases
    def __init__(self, message_bus):
        the_config = Config()
        self.hostname = the_config.value('authorization', 'leases_server')
        self.port = the_config.value('authorization', 'leases_port')
        self.message_bus = message_bus
        self.leases = None
        self.login = os.getlogin()

    def __repr__(self):
        if self.leases is None:
            return "<Leases from omf_sfa://{}:{} - **(UNFETCHED)**>"\
                .format(self.hostname, self.port)
        else:
#            return "<Leases from omf_sfa://{}:{} - fetched at {} - {} lease(s)>"\
#                .format(self.hostname, self.port, self.fetch_time, len(self.leases))
            return "<Leases from omf_sfa://{}:{} - {} lease(s)>"\
                .format(self.hostname, self.port, len(self.leases))

    @asyncio.coroutine
    def feedback(self, field, msg):
        yield from self.message_bus.put({field: msg})

    def has_special_privileges(self):
        # the condition on login is mostly for tests
        return self.login == 'root' and os.getuid() == 0

    @asyncio.coroutine
    def is_valid(self):
        if self.has_special_privileges():
            return True
        try:
            yield from self.fetch()
            return self._is_valid(self.login)
        except Exception as e:
            yield from self.feedback('info', "Could not fetch leases : {}".format(e))
            return False

# TCPConnector with verify_ssl = False
# or ProxyConnector (that inherits TCPConnector) ?
    @asyncio.coroutine
    def fetch(self):
        if self.leases is not None:
            return
        self.leases = []
        self.fetch_time = time.strftime("%Y-%m-%d @ %H:%M")
        try:
            if debug: print("Leases are being fetched")
            connector = aiohttp.TCPConnector(verify_ssl=False)
            url = "https://{}:{}/resources/leases".format(self.hostname, self.port)
            response = yield from aiohttp.get(url, connector=connector)
            text = yield from response.text()
            omf_sfa_answer = json.loads(text)
            if debug: print("Leases received answer {}".format(omf_sfa_answer))
            resources = omf_sfa_answer['resource_response']['resources']
            # we should keep only the non-broken ones but until we are confident
            # that debugging is over, et's be cautious
            self.leases = [ Lease(resource) for resource in resources ]
            self.leases.sort(key=Lease.sort_key)
                
        except Exception as e:
            if debug: print("Leases.fetch: exception {}".format(e))
            yield from self.feedback('leases_error', 'cannot get leases from {} - exception {}'
                                     .format(self, e))
        
    def _is_valid(self, login):
        # must have run fetch() before calling this
        return any([lease.is_valid(login) for lease in self.leases])

    # this can be used with a fake message queue, it's synchroneous
    def print(self):
        print(5*'-', self,
              "with special privileges" if self.has_special_privileges() else "")
        if self.leases is not None:
            for i, lease in enumerate(self.leases):
                print("{:2d} {}: {}"
                      .format(i+1, "^^" if lease.is_valid(self.login) else "..", lease))

# micro test
if __name__ == '__main__':
    import sys
    @asyncio.coroutine
    def foo(leases, login):
        print("leases {}".format(leases))
        valid = yield from leases.is_valid()
        print("valid = {}".format(valid))
        leases.print()
    def test_one_login(leases, login):
        print(10*'=', "Testing for login={}".format(login))
        asyncio.get_event_loop().run_until_complete(foo(leases, login))

    leases = Leases(asyncio.Queue())
    builtin_logins = ['root', 'someoneelse', 'onelab.inria.foo1']
    arg_logins = sys.argv[1:]
    for login in arg_logins + builtin_logins:
        test_one_login(leases, login)
