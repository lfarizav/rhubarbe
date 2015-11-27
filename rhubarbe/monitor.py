import time
import asyncio

# (linux) pip3 install progressbar33
# (macos) pip3 install py3-progressbar
import progressbar

import rhubarbe.util as util
from rhubarbe.logger import logger

# message_bus is just an asyncio.Queue

# a monitor instance comes with a hash
# 'ip' -> MonitorNode
# in its simplest form a MonitorNode just has
# a hostname (retrieved from ip through inventory)
# a rank in the nodes list (starting at 0)
# a percentage

class MonitorNode:
    def __init__(self, name, rank):
        self.name = name
        self.rank = rank
        self.percent = 0

class Monitor:
    def __init__(self, nodes, message_bus):
        self.message_bus = message_bus
        self.nodes = nodes
        #
        self._alive = True
        self._start_time = None
        # this will go from 0 to 100*len(self.nodes)
        self.total_percent = 0
        # correspondance ip -> monitor_node
        self._monitor_node_by_ip = {}
        self.goodbye_message = None
        # for the basic monitoring : we use a ingle global progress bar
        self.pbar = None
        
    def get_monitor_node(self, ip):
        # if we have it already
        if ip in self._monitor_node_by_ip:
            return self._monitor_node_by_ip[ip]
        
        # in case the incoming ip is a the reboot ip
        from rhubarbe.inventory import the_inventory
        control_ip = the_inventory.control_ip_from_any_ip(ip)
        # locate this in the subject nodes list
        for rank, node in enumerate(self.nodes):
            if node.control_ip_address() == control_ip:
                self._monitor_node_by_ip[ip] = MonitorNode(node.control_hostname(), rank)
                return self._monitor_node_by_ip[ip]

    @asyncio.coroutine
    def run(self):
        self.start_hook()
        self._start_time = time.time()
        
        while self._alive:
            message = yield from self.message_bus.get()
            if message == 'END-MONITOR':
                self._alive = False
                break
            self.dispatch(message)
            # this is new in 3.4.4
            if 'task_done' in dir(self.message_bus):
                self.message_bus.task_done()

    @asyncio.coroutine
    def stop(self):
        yield from self.message_bus.put("END-MONITOR")
        self.stop_hook()

    def stop_nowait(self):
        if self._alive:
            self._alive = False
            self.stop_hook()

    def dispatch(self, message):
        timestamp = time.strftime("%H:%M:%S")
        # in case the message is sent before the event loop has started
        duration = "+{:03}s".format(int(time.time()-self._start_time)) \
          if self._start_time is not None \
          else 5*'-'
        if isinstance(message, dict) and 'ip' in message:
            ip = message['ip']
            node = self.get_monitor_node(ip)
            if 'tick' in message:
                self.dispatch_ip_tick_hook(ip, node, message, timestamp, duration)
            elif 'percent' in message:
                # compute delta, update node.percent and self.total_percent
                node_previous_percent = node.percent
                node_current_percent = message['percent']
                delta = node_current_percent - node_previous_percent
                node.percent = node_current_percent
                self.total_percent += delta
                logger.info("{} percent: {}/100 (was {}), total {}/{}"
                            .format(node.name, node_current_percent, node_previous_percent,
                                    self.total_percent, 100*len(self.nodes)))
                self.dispatch_ip_percent_hook(ip, node, message, timestamp, duration)
            else:
                self.dispatch_ip_hook(ip, node, message, timestamp, duration)
        else:
            self.dispatch_hook(message, timestamp, duration)

    def message_to_text(self, message):
        if not isinstance(message, dict):
            # should not happen
            return "LITTERAL" + str(message)
        elif 'info' in message:
            return message['info']
        elif 'loading_image' in message:
            return "Loading image {}".format(message['loading_image'])
        elif 'selected_nodes' in message:
            names = message['selected_nodes'].node_names()
            return ("Selection: " + " ".join(names)) if names else "Empty Node Selection"
        else:
            return str(message)

    subkeys = ['frisbee_retcod', 'reboot', 'ssh_status', 'frisbee_status']

    def message_to_text_ip(self, message, node, mention_node=True):
        text = None
        if 'percent' in message:
            text = "{:02}".format(message['percent'])
        elif 'frisbee_retcod' in message:
            text = "Uploading successful" if message['frisbee_retcod'] == 0 else "Uploading FAILED !"
        else:
            for key in self.subkeys:
                if key in message:
                    text = "{} = {}".format(key, message[key])
                    break
        if text is None:
            text = str(message)
        return text if mention_node == False else "{} : {}".format(node.name, text)

    def set_goodbye(self, message):
        self.goodbye_message = message

    #################### specifics of the basic monitor 
    def start_hook(self):
        pass
    def stop_hook(self):
        if self.goodbye_message:
            print(self.goodbye_message)
    def dispatch_hook(self, message, timestamp, duration):
        text = self.message_to_text(message)
        print("{} - {}: {}".format(timestamp, duration, text))

    def dispatch_ip_hook(self, ip, node, message, timestamp, duration):
        text = self.message_to_text_ip(message, node, mention_node=False)
            
        print("{} - {}: {} {}".format(timestamp, duration, node.name , text))

    def dispatch_ip_percent_hook(self, ip, node, message, timestamp, duration):
        # start progressbar
        if self.pbar is None:
            widgets = [ progressbar.Bar(), 
                        progressbar.Percentage(), ' |',
                        progressbar.FormatLabel('%(seconds).2fs'), '|',
                        progressbar.ETA(),
                      ]
            self.pbar = \
                progressbar.ProgressBar(widgets = widgets,
                                        maxval = len(self.nodes)*100)
            self.pbar.start()
        self.pbar.update(self.total_percent)
        if self.total_percent == len(self.nodes)*100:
            self.pbar.finish()

    def dispatch_ip_tick_hook(self, ip, node, message, timestamp, duration):
        # start progressbar
        if self.pbar is None:
            widgets = [ 'Collecting image : ',
#                        progressbar.BouncingBar(marker=progressbar.RotatingMarker()),
                        progressbar.BouncingBar(marker='*'),
                        progressbar.FormatLabel(' %(seconds).2fs'),
                    ]
            self.pbar = \
                progressbar.ProgressBar(widgets = widgets,
                                        maxval = progressbar.UnknownLength)
            self.pbar.start()
            # progressbar is willing to work as expected here with maxval=UnknownLength
            # but still insists to get a real value apparently
            self.value = 0
        self.value += 1
        self.pbar.update(self.value)
        # hack way to finish the progressbar since we have no other way to figure it out
        if message['tick'] == 'END':
            self.pbar.finish()
