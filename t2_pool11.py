from queue import Queue
from random import shuffle
import threading
import time
import easysnmp
from multiprocessing.pool import Pool

KILL_FLAG = False

COMMUNITY_LIST = ['GPR',
                  'GPR_COMMUNITY_GET'] # TODO config file
        
SNMP_ERROR = 'PyEval_EvalFrameEx returned a result with an error set'
        
oids = {
    'OID_CDP'         : '1.3.6.1.4.1.9.9.23.1.2.1.1',
    'CDP_NEIGH'       : '1.3.6.1.4.1.9.9.23.1.2.1.1.6',
    'CDP_NEIGH_PORTS' : '1.3.6.1.4.1.9.9.23.1.2.1.1.7',
    'CDP_NEIGH_IPS'   : '1.3.6.1.4.1.9.9.23.1.2.1.1.4',
    'OID_LLDP'        : '1.0.8802.1.1.2.1.4',
    'OID_SYSDESCR'    : '1.3.6.1.2.1.1.1.0'
}

def test(element):
    community, ip = element
    try:
        s = easysnmp.session.Session(hostname=ip,
                                    version=2,
                                    community=community,
                                    timeout=1,
                                    retries=2,
                                    remote_port=161)
        _ = s.bulkwalk(oids=oids['OID_SYSDESCR'])
        return community
    except Exception:
        return ''

class Machine():
    def __init__(self, name='', ip='', valid_community=''):
        self.name            = name
        self.ip              = ip
        self.valid_community = valid_community
        self.neighbors_nodes = {}
        
class Controller():
    def __init__(self, _queue=None, buf_size=10, root='127.0.0.1'):
        self.__queue_ips_to_analyse = Queue(buf_size)
        self.__queue_ips_discovered = Queue(buf_size)
        m = Machine(ip=root, name='root')
        self.__queue_ips_to_analyse.put(m)
        self.valid_ips = {}
        self.nodes = {}
        
    def get_from_discovered(self):
        return self.__queue_ips_discovered.get()
    
    def get_from_analyse(self):
        return self.__queue_ips_to_analyse.get()
    
    def put_on_analyse(self, element):
        self.__queue_ips_to_analyse.put((element))
        
    def put_on_discovered(self, element):
        self.__queue_ips_discovered.put((element))
    
class ProducerThread(threading.Thread):
    
    def __init__(self, name=None, controller=None):
        super(ProducerThread,self).__init__()
        self.name   = name
        self.queue  = controller

    def run(self):
        while True:
            
            if KILL_FLAG: break
            
            machine = self.queue.get_from_discovered()
            
            ip = machine.ip
            name = machine.name
            
            current_valid_ips = list(self.queue.nodes.keys())
            
            if ip not in current_valid_ips:
                self.queue.valid_ips[ip] = machine
                print('[+]', '('+ip+')', name); print(self.queue.nodes)
                self.queue.put_on_analyse(machine)
            else:
                # TODO verificação ip
                pass
            
            time.sleep(1)
        return

class ConsumerThread(threading.Thread):
    
    def __init__(self, name=None, controller=None, community_discover_threads=4):
        super(ConsumerThread,self).__init__()
        self.name   = name
        self.queues = controller
        self.session = None
        self.valid_community = ''
        self.community_discover_threads = community_discover_threads

    def connect(self, machine_obj):
        
        ip = machine_obj.ip
    
        shuffle(COMMUNITY_LIST)
        
        FUNC_ARGS = [(x, ip) for x in COMMUNITY_LIST]
    
        pool = Pool(self.community_discover_threads)
        community_list = pool.map(test, FUNC_ARGS)
        community_list = set(community_list)
        
        community = ''
        for i in community_list:
            if i != '':
                community = i
                break

        self.session = easysnmp.session.Session(hostname=ip,
                                                version=2,
                                                community=community,
                                                timeout=1,
                                                retries=2,
                                                remote_port=161)

    def CDP_neighbors(self):
        response = []
        sess = self.session
        
        try:
            r = sess.bulkwalk(oids=oids['CDP_NEIGH'])
        except SystemError as se:
            if SNMP_ERROR in se.args:
                return []
        
        for j in r:
            value = j.value
            response.append(value)
        return response

    def CDP_ips(self):
        response = []

        try:
            r = self.session.bulkwalk(oids=oids['CDP_NEIGH_IPS'])
        except SystemError as se:
            if SNMP_ERROR in se.args:
                return []
    
        for j in r:
            ip = list(map(ord,j.value))
            ip = [str(i) for i in ip]
            ip = '.'.join(ip)
            value = ip
            response.append(value)
        return response
    
    def CDP_ports(self):
        response = []
        
        try:
            r = self.session.bulkwalk(oids=oids['CDP_NEIGH_PORTS'])
        except SystemError as se:
            if SNMP_ERROR in se.args:
                return []
        
        for j in r:
            value = j.value
            response.append(value)
        return response
    
    def run(self):
        while True:
            
            if KILL_FLAG: break
            
            machine_obj = self.queues.get_from_analyse()
            
            self.connect(machine_obj)
            
            machine_obj.valid_community = self.session.community
            
            ips             = self.CDP_ips()
            neighbors       = self.CDP_neighbors()
            interfaces      = self.CDP_ports()
            
            relation = zip(ips, neighbors, interfaces)
            relation = list(relation)
            
            for ip,name,interface in relation:
                new_machine = Machine(name, ip)
                self.queues.nodes[machine_obj.ip] = (ip,name, interface)
                self.queues.put_on_discovered(new_machine)
            
            self.session = None
            time.sleep(1)

if __name__ == '__main__':
    
    # TODO load_config()
    
    NUM_PRODUCERS   = 1
    NUM_CONSUMERS   = 1
    NUM_COM_THREADS = len(COMMUNITY_LIST)
    
    print('Producers threads', NUM_PRODUCERS)
    print('Workers threads', NUM_CONSUMERS)
    print('Community test threads', NUM_COM_THREADS)
     
    controller = Controller(buf_size=10, root='192.168.16.253')
     
    for i in range(NUM_PRODUCERS):
        productor = ProducerThread(name='Producer'+str(i),
                                   controller=controller)
        productor.start()
         
    for i in range(NUM_CONSUMERS):
        consumer  = ConsumerThread(name='Worker'+str(i),
                                   controller=controller,
                                   community_discover_threads=NUM_COM_THREADS)
        consumer.start()





