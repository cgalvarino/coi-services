#!/usr/bin/env python

"""
@package  ion.services.sa.instrument.agent_status_builder
@author   Ian Katz
"""
from ion.util.enhanced_resource_registry_client import EnhancedResourceRegistryClient
from ooi.logging import log
from pyon.agent.agent import ResourceAgentClient
from pyon.core.bootstrap import IonObject
from pyon.core.exception import NotFound, Unauthorized, BadRequest

from interface.objects import ComputedValueAvailability, ComputedIntValue, ComputedDictValue, ComputedListValue
from interface.objects import AggregateStatusType, DeviceStatusType
from pyon.ion.resource import RT, PRED
from pyon.util.containers import DotDict

# possible ways of determining the type of a device driver
DriverTypingMethod = DotDict()
DriverTypingMethod.ByRR = 1
DriverTypingMethod.ByAgent = 2
DriverTypingMethod.ByException = 3

class AgentStatusBuilder(object):

    def __init__(self, process=None):
        """
        the process should be the "self" of a service instance
        """
        assert process
        self.process = process
        self.dtm = DriverTypingMethod.ByRR
        self.RR2 = None

        # make an internal pointer to this function so we can Mock it for testing
        self._get_agent_client = ResourceAgentClient

        if DriverTypingMethod.ByRR == self.dtm:
            self.RR2 = EnhancedResourceRegistryClient(process.clients.resource_registry)


    def set_status_computed_attributes(self, computed_attrs, values_dict=None, availability=None, reason=None):
        mappings = {AggregateStatusType.AGGREGATE_COMMS    : "communications_status_roll_up",
                    AggregateStatusType.AGGREGATE_POWER    : "power_status_roll_up",
                    AggregateStatusType.AGGREGATE_DATA     : "data_status_roll_up",
                    AggregateStatusType.AGGREGATE_LOCATION : "location_status_roll_up"}

        if values_dict is None:
            values_dict = {}

        for k, a in mappings.iteritems():
            if k in values_dict:
                status = ComputedIntValue(status=availability, value=values_dict[k], reason=reason)
            else:
                if None is reason: reason = "%s not in %s" % (k, values_dict)
                status = ComputedIntValue(status=ComputedValueAvailability.NOTAVAILABLE, reason=reason)
            setattr(computed_attrs, a, status)


    def set_status_computed_attributes_notavailable(self, computed_attrs, reason):
        self.set_status_computed_attributes(computed_attrs, None, ComputedValueAvailability.NOTAVAILABLE, reason)
        if hasattr(computed_attrs, "child_device_status"):
            computed_attrs.child_device_status = ComputedDictValue(status=ComputedValueAvailability.NOTAVAILABLE,
                                                                   reason=reason)


    def get_device_agent(self, device_id):
        if not device_id or device_id is None:
            return None, "No device ID was provided"

        try:
            h_agent = self._get_agent_client(device_id, process=self.process)
            log.debug("got the agent client here: %s for the device id: %s and process: %s",
                      h_agent, device_id, self.process)
        except NotFound:
            return None, "Could not connect to agent instance -- may not be running"

        except Unauthorized:
            return None, "The requester does not have the proper role to access the status of this agent"

        except AttributeError:
            return None, "Could not find an agent instance for this device id"

        return h_agent, ""


    # get a lookup table that includes child_agg_status + the parent device status as dev_id -> {AggStatusType: DeviceStatusType}
    def get_cumulative_status_dict(self, device_id, child_device_ids=None, status_dict=None):



        h_agent, reason = self.get_device_agent(device_id)
        log.trace("Got h_agent = %s, reason = %s", h_agent, reason)
        if None is h_agent:
            log.warn('no agent for device %s, reason=%s', device_id, reason)
            return None, reason

        if status_dict and device_id in status_dict:
            this_status = status_dict.get(device_id, {})
        else:


            # read child agg status
            try:
                #retrieve the platform status from the platform agent
                this_status = h_agent.get_agent(['aggstatus'])['aggstatus']
                log.debug("this_status for %s is %s", device_id, this_status)

            except Unauthorized:
                log.warn("The requester does not have the proper role to access the status of this agent")
                return None, "InstrumentDevice(get_agent) has been denied"

        out_status = {device_id: this_status}

        if DriverTypingMethod.ByAgent == self.dtm:
            # we're done if the agent doesn't support child_agg_status
            if not "child_agg_status" in [c.name for c in h_agent.get_capabilities()]:
                return out_status, None
        elif DriverTypingMethod.ByRR == self.dtm:
            device_obj = self.RR2.read(device_id)
            if RT.PlatformDevice != device_obj._get_type():
                return out_status, None

        try:
            child_agg_status = h_agent.get_agent(['child_agg_status'])['child_agg_status']
            log.debug('get_cumulative_status_dict child_agg_status : %s', child_agg_status)
            if child_agg_status:
                out_status.update(child_agg_status)
            return out_status, None
        except Unauthorized:
            log.warn("The requester does not have the proper role to access the child_agg_status of this agent")
            return out_status, "Error getting child status: 'child_agg_status' has been denied"


    #return this aggregate status, reason for fail, dict of device_id -> agg status
    def get_device_rollup_statuses_and_child_agg_status(self, device_id, child_device_ids=None, warn_missing=True,
                                                        status_dict=None):



        master_status_dict, reason = self.get_cumulative_status_dict(device_id,status_dict=status_dict)

        log.debug("Got master_status_dict = %s, reason = %s", master_status_dict, reason)
        if None is master_status_dict:
            return None, reason, None

        # no rolling up necessary for instruments (no child_device_ids list)
        if None is child_device_ids:
            return master_status_dict[device_id], None, None

        # if child device ids is not none, the developer had better be giving us a list
        assert isinstance(child_device_ids, list)

        log.debug("Computing rollup of devices%s in status from %s", child_device_ids + [device_id], master_status_dict)
        rollup_statuses = {}
        # 1. loop through the items in this device status,
        # 2. append all the statuses of that type from child devices,
        # 3. crush
        for stype, svalue in AggregateStatusType._str_map.iteritems():
            if not device_id in master_status_dict:
                log.warn("parent device %s not found in master_status_dict", device_id)
                this_status = {}
            else:
                this_status = master_status_dict[device_id]

            one_type_status_list = [this_status.get(stype, DeviceStatusType.STATUS_UNKNOWN)]
            for child_device_id in child_device_ids:
                if not child_device_id in master_status_dict:
                    if warn_missing:
                        log.warn("Child device '%s' of parent device '%s' not found in master_status_dict",
                                 child_device_id, device_id)
                else:
                    # get the dict of AggregateStatusType -> DeviceStatusType
                    child_statuses = master_status_dict[child_device_id]
                    one_type_status_list.append(child_statuses.get(stype, DeviceStatusType.STATUS_UNKNOWN))

            rollup_statuses[stype] = self._crush_status_list(one_type_status_list)

        log.debug("combined_status is %s", rollup_statuses)

        return rollup_statuses, None, master_status_dict



    # child_device_ids is None for instruments, a list for platforms
    def add_device_rollup_statuses_to_computed_attributes(self, device_id, extension_computed, child_device_ids=None,
                                                          status_dict=None):

        rollup_statuses, reason, child_agg_status = self.get_device_rollup_statuses_and_child_agg_status(device_id,
                                                                                                     child_device_ids,
                                                                                                     status_dict=status_dict)
        log.debug("Got rollup_statuses = %s, reason = %s", rollup_statuses, reason)

        if None is rollup_statuses:
            log.debug("setting status notavailable")
            self.set_status_computed_attributes_notavailable(extension_computed, reason)

            if hasattr(extension_computed, "child_device_status"):
                extension_computed.child_device_status = ComputedDictValue(status=ComputedValueAvailability.NOTAVAILABLE,
                                                                           reason=reason)
            return None

        # no rolling up necessary for instruments (no child_device_ids list)
        if None is child_device_ids:
            self.set_status_computed_attributes(extension_computed, rollup_statuses,
                                                ComputedValueAvailability.PROVIDED)
            return None

        # get child agg status if we can set child_device_status
        # todo: split into instrument and platform
        if child_agg_status and hasattr(extension_computed, "child_device_status"):
            crushed = dict([(k, self._crush_status_dict(v)) for k, v in child_agg_status.iteritems()])
            extension_computed.child_device_status = ComputedDictValue(status=ComputedValueAvailability.PROVIDED,
                                                                       value=crushed)


        self.set_status_computed_attributes(extension_computed, rollup_statuses,
                                            ComputedValueAvailability.PROVIDED)

        return child_agg_status

    def _crush_status_dict(self, values_dict):
        log.debug("crushing dict %s", values_dict)
        return self._crush_status_list(values_dict.values())


    def _crush_status_list(self, values_list):
        # reported status is worst (highest # value) of the component values
        status = max(values_list) if values_list else DeviceStatusType.STATUS_UNKNOWN
        log.debug("crushing list %s to value %s", values_list, status)
        return status


    #                                               keys becomes aggregate statuses in the same order
    def compute_status_list(self, child_agg_status, keys):
        ret = []
        if not isinstance(child_agg_status, dict):
            return ComputedListValue(reason="Top platform's child_agg_status is '%s'" % type(child_agg_status).__name__)

        for k in keys:
            # map None to UNKNOWN
            #if not type("") == type(k):
            #    raise BadRequest("attempted to compute_status_list with type(v) = %s : %s" % (type(k), k))
            if k in child_agg_status:
                ret.append(self._crush_status_dict(child_agg_status[k]))
            else:
                log.warn("Status for device '%s' not found in parent platform's child_agg_status", k)
                ret.append(DeviceStatusType.STATUS_UNKNOWN)

        return ComputedListValue(status=ComputedValueAvailability.PROVIDED,
                                 value=ret)


    def obtain_agent_calculation(self, device_id, result_container):
        ret = IonObject(result_container)

        h_agent, reason = self.get_device_agent(device_id)
        if None is h_agent:
            ret.status = ComputedValueAvailability.NOTAVAILABLE
            ret.reason = reason
        else:
            ret.status = ComputedValueAvailability.PROVIDED

        return h_agent, ret


    def _get_status_of_device(self, device_id):

        a_client, reason = self.get_device_agent(device_id)
        if None is a_client:
            return None, reason
        try:
            aggstatus = a_client.get_agent(['aggstatus'])['aggstatus']
            log.debug('get_aggregate_status_of_device status: %s', aggstatus)
            return aggstatus, ""
        except Unauthorized:
            log.warn("The requester does not have the proper role to access the status of this agent")
            return None, "InstrumentDevice(get_agent) has been denied"

    def get_status_of_device(self, device_id):
        status, _ = self._get_status_of_device(device_id)
        if None is status:
            status = {}
        return status


    def get_aggregate_status_of_device(self, device_id):
        if  device_id is not None and type("") != type(device_id):
            errmsg = "get_aggregate_status_of_device passed bad type %s" % type(device_id)
            log.warn(errmsg)
            raise BadRequest(errmsg)

        aggstatus, reason = self._get_status_of_device(device_id)

        if None is aggstatus:
            DeviceStatusType.STATUS_UNKNOWN
        else:
            return self._crush_status_dict(aggstatus)


