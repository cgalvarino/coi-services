
from pyon.ion.transform import TransformFunction
from pyon.service.service import BaseService
from pyon.core.exception import BadRequest
from pyon.public import IonObject, RT, log

import time
import numpy
from ion.services.dm.utility.granule.taxonomy import TaxyTool
from ion.services.dm.utility.granule.record_dictionary import RecordDictionaryTool
from pyon.util.containers import get_safe
from prototype.sci_data.stream_defs import SBE37_CDM_stream_definition, SBE37_RAW_stream_definition

from coverage_model.parameter import ParameterDictionary, ParameterContext
from coverage_model.parameter_types import QuantityType
from coverage_model.basic_types import AxisTypeEnum
import numpy as np

import StringIO
from numpy import array, append
from interface.services.dm.ipubsub_management_service import PubsubManagementServiceProcessClient
from pyon.ion.transforma import TransformDataProcess
from ion.core.function.transform_function import SimpleGranuleTransformFunction

# Matplotlib related imports
# Need try/catch because of weird import error
try:
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
    from matplotlib.figure import Figure
except:
    import sys
    print >> sys.stderr, "Cannot import matplotlib"

tx = TaxyTool()
tx.add_taxonomy_set('matplotlib_graphs','Matplotlib generated graphs for a particular data product')

class VizTransformMatplotlibGraphs(TransformDataProcess):

    """
    This class is used for instantiating worker processes that have subscriptions to data streams and convert
    incoming data from CDM format to Matplotlib graphs

    """


    def on_start(self):

        self.pubsub_management = PubsubManagementServiceProcessClient(process=self)

        self.stream_info  = self.CFG.get_safe('process.publish_streams',{})
        self.stream_names = self.stream_info.keys()
        self.stream_ids   = self.stream_info.values()

        if not self.stream_names:
            raise BadRequest('MPL Transform has no output streams.')


        super(VizTransformMatplotlibGraphs,self).on_start()

    def recv_packet(self, packet, in_stream_route, in_stream_id):
        log.info('Received packet')
        print type(packet)
        outgoing = VizTransformMatplotlibGraphsAlgorithm.execute(input=packet, params=self.get_stream_definition())
        for stream_name in self.stream_names:
            publisher = getattr(self, stream_name)
            publisher.publish(outgoing)

    def get_stream_definition(self):
        stream_id = self.stream_ids[0]
        stream_def = self.pubsub_management.read_stream_definition(stream_id=stream_id)
        return stream_def._id


class VizTransformMatplotlibGraphsAlgorithm(SimpleGranuleTransformFunction):
    @classmethod
    @SimpleGranuleTransformFunction.validate_inputs
    def execute(cls, input=None, context=None, config=None, params=None, state=None):
        log.debug('Matplotlib transform: Received Viz Data Packet')
        stream_definition_id = params

        #init stuff

        # parse the incoming data
        rdt = RecordDictionaryTool.load_from_granule(input)

        vardict = {}
        vardict['time'] = get_safe(rdt, 'time')
        vardict['conductivity'] = get_safe(rdt, 'conductivity')
        vardict['pressure'] = get_safe(rdt, 'pressure')
        vardict['temperature'] = get_safe(rdt, 'temp')

        vardict['longitude'] = get_safe(rdt, 'lon')
        vardict['latitude'] = get_safe(rdt, 'lat')
        vardict['height'] = get_safe(rdt, 'height')
        arrLen = len(vardict['time'])

        # init the graph_data structure for storing values
        graph_data = {}
        for varname in vardict.keys():    #psd.list_field_names():
            graph_data[varname] = []


        # If code reached here, the graph data storage has been initialized. Just add values
        # to the list
        for varname in vardict.keys():  # psd.list_field_names():
            if vardict[varname] == None:
                # create an array of zeros to compensate for missing values
                graph_data[varname].extend([0.0]*arrLen)
            else:
                graph_data[varname].extend(vardict[varname])

        out_granule = cls.render_graphs(graph_data, stream_definition_id)

        return out_granule

    @classmethod
    def render_graphs(cls, graph_data, stream_definition_id):

        # init Matplotlib
        fig = Figure(figsize=(8,4), dpi=200, frameon=True)
        ax = fig.add_subplot(111)
        canvas = FigureCanvas(fig)
        imgInMem = StringIO.StringIO()

        # If there's no data, wait
        # For the simple case of testing, lets plot all time variant variables one at a time
        xAxisVar = 'time'
        xAxisFloatData = graph_data[xAxisVar]

        # Prepare the set of y axis variables that will be plotted. This needs to be smarter and passed as
        # config variable to the transform
        yAxisVars = []
        for varName, varData in graph_data.iteritems():
            if varName == 'time' or varName == 'height' or varName == 'longitude' or varName == 'latitude':
                continue
            yAxisVars.append(varName)

        idx = 0
        for varName in yAxisVars:
            yAxisFloatData = graph_data[varName]

            # Generate the plot
            ax.plot(xAxisFloatData, yAxisFloatData, cls.line_style(idx), label=varName)
            idx += 1

        yAxisLabel = ""
        # generate a filename for the output image
        for varName in yAxisVars:
            if yAxisLabel:
                yAxisLabel = yAxisLabel + "-" + varName
            else:
                yAxisLabel = varName

        fileName = yAxisLabel + '_vs_' + xAxisVar + '.png'

        ax.set_xlabel(xAxisVar)
        ax.set_ylabel(yAxisLabel)
        ax.set_title(yAxisLabel + ' vs ' + xAxisVar)
        ax.set_autoscale_on(False)
        ax.legend(loc='upper left')

        # Save the figure to the in memory file
        canvas.print_figure(imgInMem, format="png")
        imgInMem.seek(0)

        # Create output dictionary from the param dict
        out_rdt = RecordDictionaryTool(stream_definition_id=stream_definition_id)

        # Prepare granule content
        out_dict = {}
        out_dict["viz_product_type"] = "matplotlib_graphs"
        out_dict["image_obj"] = imgInMem.getvalue()
        out_dict["image_name"] = fileName
        out_dict["content_type"] = "image/png"

        out_rdt["mpl_graph"] = np.array([out_dict])
        return out_rdt.to_granule()


    # This method picks out a matplotlib line style based on an index provided. These styles are set in an order
    # as a utility. No other reason
    @classmethod
    def line_style(cls, index):

        color = ['b','g','r','c','m','y','k','w']
        stroke = ['-','--','-.',':','.',',','o','+','x','*']

        style = color[index % len(color)] + stroke [(index / len(color)) % len(stroke)]

        return style
