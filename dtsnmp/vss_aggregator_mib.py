import logging
from .poller import Poller
from .processing import convert_to_readable_time
from .processing import process_metrics, reduce_average, split_oid_index

logger = logging.getLogger(__name__)


# Possible testing tool = http://snmplabs.com/snmpsim/quickstart.html#simulate-from-mib

class VSSAggregatorMIB():
    """
    Properties from VSSM-SMI-MIB.TXT

    Reference
    ../VSSM-SMI-MIB.TXT

    Usage
    vssagg_mib = VSSAggregatorMIB(device, authentication)
    property_dict = vssagg_mib.poll_properties()
    metrics_dict = vssagg_mib.poll_metrics()

    Returns Agg properties and metrics
    """

    def __init__(self, device, authentication):
        self.poller = Poller(device, authentication)

    def poll_properties(self):
        mib_properties = [
            '1.3.6.1.4.1.21671.4.1.1',  # 'productID',
            '1.3.6.1.4.1.21671.4.1.2',  # 'productVersion',
            '1.3.6.1.4.1.21671.4.2.1',  # 'numberOfPorts',
            '1.3.6.1.4.1.21671.4.2.2',  # 'chassisTemperature',
            '1.3.6.1.4.1.21671.4.2.3', 	# 'coreTemperature',
            '1.3.6.1.4.1.21671.4.2.4',  # 'numberOfPowerSupplies'
        ]
        timeout = 2
        retries = 1
        gen = self.poller.snmp_connect_bulk(mib_properties, timeout, retries)
        props = {}
        errorIndication, errorStatus, errorIndex, varBinds = next(gen)
        if errorIndication:
            raise Exception(errorIndication)
        elif errorStatus:
            raise Exception('%s at %s' % (errorStatus.prettyPrint(),
                                          errorIndex and varBinds[int(errorIndex) - 1][0] or '?'))
        else:
            get_vss_properties(varBinds, props)

        return props

    def poll_metrics(self):
        netActivity = self._poll_NetworkActivityStats()
        storage = self._poll_storage()

        cpu_utilisation = cpu.get('cpu', [])
        memory = storage.get('memory', [])
        disk = storage.get('disk', [])

        metrics = {
            'cpu_utilisation': cpu_utilisation,
            'memory_utilisation': memory,
            'disk_utilisation': disk
        }
        return metrics

    def _poll_NetworkActivityStats(self):
        cpu_metrics = [
            ' .1.3.6.1.4.1.21671.4.6.1.1.2',  # naPortID
            ' .1.3.6.1.4.1.21671.4.6.1.1.3',  # naRxThroughput
            ' .1.3.6.1.4.1.21671.4.6.1.1.4',  # naTxThroughput
            ' .1.3.6.1.4.1.21671.4.6.1.1.5',  # naPeakRxThroughput
            ' .1.3.6.1.4.1.21671.4.6.1.1.6',  # naPeakTxThroughput
            ' .1.3.6.1.4.1.21671.4.6.1.1.7',  # naRxUtilization
            ' .1.3.6.1.4.1.21671.4.6.1.1.8',  # naTxUtilization
            ' .1.3.6.1.4.1.21671.4.6.1.1.11',  # naGoodPacketsRx
            ' .1.3.6.1.4.1.21671.4.6.1.1.12',  # naGoodPacketsTx
            ' .1.3.6.1.4.1.21671.4.6.1.1.13',  # naBadPacketsRx
            ' .1.3.6.1.4.1.21671.4.6.1.1.14',  # naBadPacketsTx
            ' .1.3.6.1.4.1.21671.4.6.1.1.19',  # naUnicastsRx
            ' .1.3.6.1.4.1.21671.4.6.1.1.20',  # naUnicastsTx
            ' .1.3.6.1.4.1.21671.4.6.1.1.21',  # naOverflowDropsRx
            ' .1.3.6.1.4.1.21671.4.6.1.1.22',  # naOverflowDropsTx
        ]

        gen = self.poller.snmp_connect_bulk(cpu_metrics)
        return process_metrics(gen, calculate_network_metrics)

    def _poll_PortStatus(self):
        storage_metrics = [
            '1.3.6.1.4.1.21671.4.3.1.1.2',  # portID
            '1.3.6.1.4.1.21671.4.3.1.1.3',  # portName
            '1.3.6.1.4.1.21671.4.3.1.1.4'  # port Class
            '1.3.6.1.4.1.21671.4.3.1.1.5'  # portState
            '1.3.6.1.4.1.21671.4.3.1.1.6'  # portSpeed
            '1.3.6.1.4.1.21671.4.3.1.1.7'  # portDuplex
            '1.3.6.1.4.1.21671.4.3.1.1.8'  # portError        
        ]

        gen = self.poller.snmp_connect_bulk(storage_metrics)
        return process_metrics(gen, calculate_port_metrics)


def calculate_network_metrics(varBinds, metrics):
    """
    Processing Function to be used with processing.process_metrics
    Extracts the CPU utilisation for each index
    hrProcessorLoad -> varBinds[0]
    """
    cpu = {}
    index = split_oid_index(varBinds[0][0])
    cpu['value'] = float(varBinds[0][1])
    cpu['dimension'] = {'Index': index}
    cpu['is_absolute_number'] = True
    metrics.setdefault('cpu', []).append(cpu)


def calculate_port_metrics(varBinds, metrics):
    """
    Processing Function to be used with processing.process_metrics
    Extracts the storage itilisation - splitting into memory/disk types
    hrStorageDescr -> varBinds[0]
    hrStorageSize -> varBinds[1]
    hrStorageUsed -> varBinds[2]
    """
    memory_types = ['memory', 'swap space', 'ram']

    name = varBinds[0][1].prettyPrint()
    size = float(varBinds[1][1])
    used = float(varBinds[2][1])
    utilisation = 0
    # Division by 0 exception - e.g. Swap Space 0 used of 0
    if size > 0:
        utilisation = (used / size)*100

    storage = {}
    storage['dimension'] = {'Storage': name}
    storage['value'] = utilisation
    storage['is_absolute_number'] = True

    # Memory metrics as a dimension under memory_utilisation
    if any(x in name.lower() for x in memory_types):
        metrics.setdefault('memory', []).append(storage)
    else:
        metrics.setdefault('disk', []).append(storage)


def get_vss_properties(varBinds, props):
    """
    sysDescr -> varBinds[0]
    sysObjectID -> varBinds[1]
    sysUpTime -> varBinds[2]
    sysContact -> varBinds[3]
    sysName -> varBinds[4]
    sysLocation -> varBinds[5]
    sysServices -> varBinds[6]
    sysORLastChange -> varBinds[7]
    """
    props['sysDescr'] = str(varBinds[0][1])
    props['sysObjectID'] = str(varBinds[1][1])
    props['sysUpTime'] = convert_to_readable_time(str(varBinds[2][1]))
    props['sysContact'] = str(varBinds[3][1])
    props['sysName'] = str(varBinds[4][1])
    props['sysLocation'] = str(varBinds[5][1])
    props['sysServices'] = str(varBinds[6][1])
    props['sysORLastChange'] = convert_to_readable_time(str(varBinds[7][1]))
