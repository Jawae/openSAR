# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Timeseries_SAR
                                 A QGIS plugin
 Plot timeseries data from a vrt file
                              -------------------
        begin                : 2017-05-11
        git sha              : $Format:%H$
        copyright            : (C) 2017 by Earth Big Data LLC
        email                : kitsin@earthbigdata.com
 ***************************************************************************/

"""
"""
Uses correct SAR Data power averaging. Accespts 8 bit (scaled dB) or 16bit (scaled amplitude) data
"""
import sys
if sys.platform.startswith('linux'):
    import matplotlib
    matplotlib.use("TkAgg")

from PyQt4.QtCore import *
from PyQt4.QtGui import *
from qgis.core import *
from qgis.gui import QgsMapToolEmitPoint, QgsMessageBar
# Initialize Qt resources from file resources.py
import resources
# Import the code for the dialog
from Timeseries_SAR_dialog import Timeseries_SARDialog
import os
import numpy as np
import struct
import gdal, osr
# import pandas as pd
import datetime
import csv
from matplotlib import pyplot as plt 
import matplotlib 
import glob



def lsf (srcmin,srcmax,dstmin,dstmax):
    '''Linear Scale Factors: Generate the scaling factors from in and out ranges'''
    delta=(np.float32(srcmax)-srcmin)/(dstmax-dstmin+1)
    offset=np.float32(srcmin)
    return [delta,offset]

def dB2pwr (dB,ndv=None):
    '''Convert dB to pwr data. Writes nan as NoDataValue'''
    # Make mask
    if ndv and not np.isnan(ndv):
        dB = np.ma.masked_values(dB,ndv)
    else:
        dB = np.ma.masked_invalid(dB)
    out=np.power(10.,dB/10.)
    return 'pwr', out.filled(np.nan)

def pwr2dB (pwr,ndv=None):
    '''Convert pwr to dB. Writes nan as NoDataValue.'''
    if ndv and not np.isnan(ndv):
        pwr = np.ma.masked_values(pwr,ndv)
    else:
        pwr = np.ma.masked_invalid(pwr)
    out=10.* np.log10(pwr)
    return 'dB', out.filled(np.nan)

def DN2dB (DN,lsf,ndv=None):
    '''Converts a DN to dB according to dB = DN * delta_dB + offset. Writes nan as NoDataValue'''
    if not ndv: 
        ndv=0
    DN = np.ma.masked_values(DN,ndv)
    # The casting function takes care of data outside the 8bit range
    out= np.float32(DN * lsf[0] + lsf[1])
    # set no data to nan
    return 'dB', out.filled(np.nan)

def DN2pwr ( DN, lsf, ndv=None):
    '''Converts DN data to pwr according to pwr = dB2pwr ( DN2dB (DN,delta_dB,offset) ) '''
    _, dB = DN2dB(DN,lsf,ndv)
    itype, pwr = dB2pwr( dB )
    return itype, pwr

def amp2pwr (amp,ndv=None):
    '''Convert amp to pwr data. Writes nan as NoDataValue. Assumes scaling as dB=20*log10(AMP)-83. Cal factor = 10^8.3 = 199526231'''
    CF=199526231  
    ndv = 0 if ndv==None else ndv
    # ndv = 0.0 if not ndv==None else ndv
    # Make mask
    if ndv != None and not np.isnan(ndv):
        amp = np.ma.masked_values(amp,ndv)
    else:
        amp = np.ma.masked_invalid(amp)
    pwr=np.ma.power(amp,2.)/CF
    out = np.array(pwr.data,dtype=np.float32)
    mask=~pwr.mask & (out==0.)     
    out[ mask ] = 0.000001 # HARDCODE ALERT
    out[pwr.mask] = 0.   
    return 'pwr', out


def ReadInfo(img):
    ds = gdal.Open(img, gdal.GA_ReadOnly)
    datatype=ds.GetRasterBand(1).DataType
    geo = ds.GetGeoTransform()
    proj = ds.GetProjection()
    srs=osr.SpatialReference(wkt=proj)
    unit=srs.GetAttrValue('unit')
    return ds, geo, proj, unit, datatype


def dates_from_meta(src_dataset):
    img_handle=gdal.Open(src_dataset)
    meta={}
    dates=[]
    for i in range(1,img_handle.RasterCount+1,1):
        meta[i]=img_handle.GetRasterBand(i).GetMetadata_Dict()
        if 'Date' in meta[i].keys():
            dates.append(meta[i]['Date'].replace('-',''))
    img_handle=None
    if dates:
        return dates
    else:
        return []

class Timeseries_SAR:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: An interface instance that will be passed to this class
            which provides the hook by which you can manipulate the QGIS
            application at run time.
        :type iface: QgsInterface
        """
        # Save reference to the QGIS interface
        self.iface = iface
        # initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)
        # initialize locale
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            'Timeseries_SAR_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)

            if qVersion() > '4.3.3':
                QCoreApplication.installTranslator(self.translator)

        # Create the dialog (after translation) and keep reference
        self.dlg = Timeseries_SARDialog()

        # Declare instance attributes
        self.actions = []
        self.menu = self.tr(u'&Timeseries_SAR')
        # TODO: We are going to let the user set this up in a future iteration
        self.toolbar = self.iface.addToolBar(u'Timeseries_SAR')
        self.toolbar.setObjectName(u'Timeseries_SAR')

        self.canvas = iface.mapCanvas()
        self.tool_enabled = True
        self.vlayer_id = None

        self.x_range = 5
        self.y_range = 5

        self.line=[]
        # self.line = None
        # self.line1 = None
        # self.line2 = None

        _px = None
        _py = None

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        """Get the translation for a string using Qt translation API.

        We implement this ourselves since we do not inherit QObject.

        :param message: String for translation.
        :type message: str, QString

        :returns: Translated version of message.
        :rtype: QString
        """
        # noinspection PyTypeChecker,PyArgumentList,PyCallByClass
        return QCoreApplication.translate('Timeseries_SAR', message)


    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None):
        """Add a toolbar icon to the toolbar.

        :param icon_path: Path to the icon for this action. Can be a resource
            path (e.g. ':/plugins/foo/bar.png') or a normal file system path.
        :type icon_path: str

        :param text: Text that should be shown in menu items for this action.
        :type text: str

        :param callback: Function to be called when the action is triggered.
        :type callback: function

        :param enabled_flag: A flag indicating if the action should be enabled
            by default. Defaults to True.
        :type enabled_flag: bool

        :param add_to_menu: Flag indicating whether the action should also
            be added to the menu. Defaults to True.
        :type add_to_menu: bool

        :param add_to_toolbar: Flag indicating whether the action should also
            be added to the toolbar. Defaults to True.
        :type add_to_toolbar: bool

        :param status_tip: Optional text to show in a popup when mouse pointer
            hovers over the action.
        :type status_tip: str

        :param parent: Parent widget for the new action. Defaults None.
        :type parent: QWidget

        :param whats_this: Optional text to show in the status bar when the
            mouse pointer hovers over the action.

        :returns: The action that was created. Note that the action is also
            added to self.actions list.
        :rtype: QAction
        """

        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.toolbar.addAction(action)

        if add_to_menu:
            self.iface.addPluginToMenu(
                self.menu,
                action)

        self.actions.append(action)

        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""

        icon_path = ':/plugins/Timeseries_SAR/icon.png'
        self.add_action(
            icon_path,
            text=self.tr(u'TimeSeries_SAR'),
            callback=self.run,
            parent=self.iface.mainWindow())
        # Map Tool
        self.plottool = QgsMapToolEmitPoint(self.canvas)
        #self.plottool.setAction(self.add_action)
        self.plottool.canvasClicked.connect(self.plot_request)


    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginMenu(
                self.tr(u'&Timeseries_SAR'),
                action)
            self.iface.removeToolBarIcon(action)
        # remove the toolbar
        del self.toolbar

    def adjust_range(self, geotransform, unit):
        xres=abs(geotransform[1])
        yres=abs(geotransform[5])
        if unit.lower() == 'degree':
            #conver degree to meters
            xmres = xres / 9.259259259259266e-06
            ymres = yres / 9.259259259259266e-06
        if unit.lower() in ('meters','metre','m'):
            xmres=xres
            ymres=yres        
        # we want the box around 1 hectar (10000m2)
        if xmres >= 100:
            self.x_range=1
        else:
            self.x_range=int(round(100.0/xmres))
        if ymres >= 100:
            self.y_range=1
        else: 
            self.y_range=int(round(100.0/ymres))

    def run(self):
        """Run method that performs all the real work"""
        # # show the dialog
        # self.dlg.show()
        # # Run the dialog event loop
        # result = self.dlg.exec_()
        # # See if OK was pressed
        # if result:
        #     # Do something useful here - delete the line containing pass and
        #     # substitute with your code.
        #     pass

        self.tool_enabled = True
        self.canvas.setMapTool(self.plottool) 

    def plot_request(self, pos, button=None):
        """ Request handler for QgsMapToolEmitPoint. """
        if self.tool_enabled is False:
            print 'NO PLOT FOR YOU'
            return

        # Check if user has a layer added
        if self.canvas.layerCount() == 0 or pos is None:
            self.iface.messageBar().pushMessage('Error', 
                'Please add a like-pol (HH or VV) layer before clicking...',
                level=QgsMessageBar.WARNING,
                duration=3)

        # Check currently selected layer
        layer = self.canvas.currentLayer()
        if (layer is None or not layer.isValid() or layer.type() != QgsMapLayer.RasterLayer):
            self.iface.messageBar().pushMessage('Info', 
                'Please load a raster layer (VV or HH) image before clicking',
                level=QgsMessageBar.WARNING,
                duration=2)
            return

        # check if position needs to be reprojected to layer CRS
        layer_crs = layer.crs()
        map_crs = self.canvas.mapRenderer().destinationCrs()

        if not map_crs == layer_crs:
            if self.canvas.hasCrsTransformEnabled():
                crs_transform = QgsCoordinateTransform(map_crs, layer_crs)
                try:
                    pos = crs_transform.transform(pos)
                except:
                    self.iface.messageBar().pushMessage('Error',
                        'Could not convert map CRS to layer CRS',
                        level=QgsMessageBar.WARNING,
                        duration = 5)
                    return
            else:
                self.iface.messageBar().pushMessage('Error',
                    'Could not convert map CRS to layer CRS',
                    level=QgsMessageBar.WARNING,
                    duration = 5)
                return

        # Fetch data if inside raster
        validpol=['VV','VH','HH','HV','vv','vh','hh','hv']
        # validpol=['VV','VH','vv','vh']   # HARDCODE ALERT SENTINEL
        validpol=validpol + [x.lower() for x in validpol]
        if layer and layer.extent().contains(pos):
            #Fetch data
            # Convert position into pixel location
            selectedLayers = self.iface.legendInterface().selectedLayers()
            if 'pol' not in locals():
                pol=[]
            for layer in selectedLayers:
                if layer.type() == QgsMapLayer.VectorLayer:
                    print(layer.name())
                elif layer.type() == QgsMapLayer.RasterLayer:
                    print(layer.name())
                elif layer.type() == QgsMapLayer.PluginLayer:
                    print(layer.name())
                else:
                    raise Exception('Should never happen')
                pol.append(layer.name())

            imagearr = []
            for layer in selectedLayers:
                imagearr.append(layer.source())
 
            clicked=False
            mydB=[]
            mydate=[]
            mybands=[]
            datearr=[]

            xlim=[None,None]
            for i in imagearr:
                _,xlim_tmp=max_date_range(imagearr[0])
                xlim_tmp=list(xlim_tmp)
                if xlim[0] == None:
                        xlim[0] = xlim_tmp[0]
                if xlim[1] == None:
                    xlim[1] = xlim_tmp[1]
                xlim[0] = min(xlim[0],xlim_tmp[0])
                xlim[1] = max(xlim[1],xlim_tmp[1])
            xlim=tuple(xlim)

            i=0
            for img in imagearr:
                ds, geo, proj, unit,datatype = ReadInfo(img)
                sf = lsf(-31,7.25,1,255) 
                # ###############
                # # HARDCODE ALERT TO FIX TEMPORARY DIFFERENT SCALING OF HV CHANNELS IN QUICKLOOKS
                # if geo[5] < -0.001 and i==1:
                #    sf = lsf(-38,-0.75,1,255)  # HARDCODE ALERT QUICKLOOKS SENTINEL
                # i+=1
                # ###############
                self.adjust_range(geo, unit)
                self.check_pos(pos, geo, ds)
                if self._px and self._py:
                    if not clicked:
                        self.show_click(pos, geo, proj, self.x_range, self.y_range)
                        clicked=True
                    tmpdB, tmpdate, tmpdatearr,tmpbands = self.make_val_dates(ds,img,datatype,sf) 
                    self.save_output(pos, tmpdB, tmpdatearr, img)
                    mydB.append(tmpdB)
                    mydate.append(tmpdate) 
                    datearr.append(tmpdatearr) 
                    mybands.append(tmpbands)
                else:
                    self.iface.messageBar().pushMessage('Warning',
                        'Please select a point within the raster image', 
                        level=QgsMessageBar.WARNING,
                        duration = 5)

            # Set up the plot figure
            if not plt.fignum_exists(1):
                plt.figure()
            else:
                plt.cla()

            marker=['o','o','x','x']
            color=['r','b','r','b']
            self.line=[]
            alldates_datetime=[]
            for i in range(len(imagearr)):
                tmpplt, = plt.plot(mydate[i], mydB[i], color[i],marker=marker[i])
                self.line.append(tmpplt)  
                self.line[i].axes.set_ylim(-31, 0)
                self.line[i].set_label(pol[i])   
                alldates_datetime += mydate[i]

            # Make lables and ticks, 
            # Bands will be displayed for the first layer only
            alldates_datetime = list(set(alldates_datetime))
            alldates_datetime.sort()
            xlim=(min(alldates_datetime),max(alldates_datetime))


            # Get the bands from the first layer
            allbands=['   ' for x in alldates_datetime]
            j=0
            for i in range(len(allbands)):
                if alldates_datetime[i] in mydate[0]:
                    allbands[i]='%3d' % mybands[0][j]
                    j+=1
                              
            self.line[0].axes.set_ylim(-31, 0)
            self.line[0].axes.set_xlim(xlim[0], xlim[1])
            # labels=['%s-%s-%s' % (i[:4],i[4:6],i[6:]) for i in datearr[0]]
            labels=['%4d-%02d-%02d' % (i.year,i.month,i.day) for i in alldates_datetime]
            labels=['%s %s' % (allbands[i],labels[i]) for i in range(len(labels))]

            plt.yticks(range(-30, 1, 5), fontsize=18)
            plt.ylabel('$\gamma^0$ [dB]',fontsize=18)
            plt.xticks(alldates_datetime,labels, rotation=30,ha='right',fontsize=10)
            plt.title("Geogr. Coordinates X/Y {} - Raster P/L ({} {})".format(pos,self._px,self._py,fontsize=18))
            plt.legend()
            plt.grid()
            plt.show()
            plt.draw()



                    

    def show_click(self, pos, gt, projection, x_range, y_range):
        """
        Receives QgsPoint and adds vector boundary of raster pixel clicked
        """

        # last_selected = self.iface.activeLayer()

        # Get raster pixel px py for pos
        px = int(round((pos[0] - gt[0]) / gt[1]))
        py = int(round((pos[1] - gt[3]) / gt[5]))

        # Upper left coordinates of the pixel 
        ulx = (gt[0] + px * gt[1] + py * gt[2])
        uly = (gt[3] + px * gt[4] + py * gt[5])

        # Upper left coordinates of the buffer around the pixel
        b_ulx = ulx - int(x_range/2) * gt[1]
        b_uly = uly - int(y_range/2) * gt[5]

        # Creat Geometry
        gSquare = QgsGeometry.fromPolygon([[
            QgsPoint(b_ulx, b_uly), # uper left
            QgsPoint(b_ulx + gt[1]*x_range, b_uly), # upper right
            QgsPoint(b_ulx + gt[1]*x_range, b_uly + gt[5]*y_range), # lower right
            QgsPoint(b_ulx, b_uly + gt[5]*y_range) # lower left
            ]])

        if self.vlayer_id is not None:
            # Update the vector layer to new clicked pixel
            try:
                vlayer = QgsMapLayerRegistry.instance().mapLayers()[self.vlayer_id]
                vlayer.startEditing()
                pr = vlayer.dataProvider()
                attrs = pr.attributeIndexes()
                for feat in vlayer.getFeatures():
                    vlayer.changeAttributeValue(feat.id(), 0, py)
                    vlayer.changeAttributeValue(feat.id(), 1, px)
                    vlayer.changeGeometry(feat.id(), gSquare)
                    vlayer.updateExtents()
                vlayer.commitChanges()
                vlayer.triggerRepaint()
            except:
                self.vlayer_id=None
        else:
            # Create vector layer for clicked pixel
            uri = 'polygon?crs=%s' % projection
            vlayer = QgsVectorLayer(uri, 'Query', 'memory')
            pr = vlayer.dataProvider()
            vlayer.startEditing()
            pr.addAttributes( [ QgsField('row', QVariant.Int),
                                QgsField('col', QVariant.Int) ] ) 
            feat = QgsFeature()
            feat.setGeometry(gSquare)
            feat.setAttributes([py, px])
            pr.addFeatures([feat])
            # Symbology
            # Reference:
            # http://lists.osgeo.org/pipermail/qgis-developer/2011-April/013772.html
            props = { 'color_border'   : '255, 0, 0, 255',
                      'color'          : '255, 0, 0, 255',
                      'style'          : 'solid',
                      'style_border'   : 'solid',
                      'width'          : '1'}

            s = QgsFillSymbolV2.createSimple(props)
            vlayer.setRendererV2(QgsSingleSymbolRendererV2(s))

            # Commit and add

            vlayer.commitChanges()
            vlayer.updateExtents()

            self.vlayer_id = QgsMapLayerRegistry.instance().addMapLayer(vlayer).id()

        # Restore active layer
        # self.iface.setActiveLayer(last_selected)

    def make_val_dates(self,dataset, image,datatype=1,sf=[0.15,-31]):
        nbands = dataset.RasterCount
        imgbuf = dataset.ReadRaster(self._px,self._py,self.x_range,self.y_range)
        #imgbuf = dataset.ReadRaster(2500,2500,10,10)
        bfsize = self.x_range*self.y_range
        if datatype==1:
            myvals = struct.unpack('B'*nbands*bfsize,imgbuf)
        elif datatype==2:
            try:
                myvals = struct.unpack('H'*nbands*bfsize,imgbuf)
            except:
                print('imbuf size{}'.format(imgbuf.shape))
        elif datatype==3:
            try:
                myvals = struct.unpack('h'*nbands*bfsize,imgbuf)
            except:
                print('imbuf size{}'.format(imgbuf.shape))
        elif datatype==6:
            try:
                myvals = struct.unpack('f'*nbands*bfsize,imgbuf)
            except:
                print('imbuf size{}'.format(imgbuf.shape))
        mvalsarr = np.asarray(myvals)
        mv = mvalsarr.reshape(nbands,bfsize)

        # CONVERT TO pwr from DN ( datatype 1 = 8 bit input or AMP ( datatype 2 = 16bit input)
        if datatype==1:
            _,mvpwr = DN2pwr(mv,sf)
        elif datatype == 2:
            _,mvpwr = amp2pwr(mv,0)
        elif datatype == 3:
            _,mvpwr = amp2pwr(mv,0)
        elif datatype == 4:
            mvpwr = mv
        else:
            mvpwr = mv
        mymean = mvpwr.mean(axis=1)
        _,mydB = pwr2dB(mymean)

        # Try getting the dates from the image metadata or a .dates file
        dates=dates_from_meta(image)
        if not dates:
            # read dates file
            fdates=os.path.splitext(image)[0]+'.dates'
            with open(fdates, 'r') as d:
                dates = d.readlines()

        datearr=[s.strip('\n') for s in dates]
        # mydate=pd.to_datetime(datearr)
        mydate=[datetime.datetime.strptime(x,'%Y%m%d') for x in datearr]

        mask=np.isfinite(mydB)
        mydate2 = list(np.array(mydate)[mask])
        datearr2 = list(np.array(datearr)[mask])
        mydB2= list(mydB[mask])
        bands= [x+1 for x in list(range(nbands))]
        bands2=list(np.array(bands)[mask])

        return mydB2, mydate2, datearr2, bands2


    def check_pos(self, pos, geo, ds):            
        x_size = ds.RasterXSize
        y_size = ds.RasterYSize
        px = int(round((pos[0] - geo[0]) / geo[1]))
        py = int(round((pos[1] - geo[3]) / geo[5]))
        # Adjust for buffer, i.e. x_range/2 and take care of edges
        px -= self.x_range/2
        py -= self.y_range/2
        # # HARDCODE OVERRIDE for 10x10 DELETE AGAIN!!!
        # px -=5
        # py -=5
        # self.x_range=10
        # self.y_range=10
        # ########
        if px < 0: px=0
        if py < 0: px=0
        if px+self.x_range>x_size: px = x_size - self.x_range
        if py+self.y_range>y_size: py = y_size - self.y_range
       

        """geotransform tuple key:
           geotransform = dataset.GetGeoTransform()
           [0] /* top left x */
           [1] /* w-e pixel resolution */
           [2] /* rotation, 0 if image is "north up" */
           [3] /* top left y */
           [4] /* rotation, 0 if image is "north up" */
           [5] /* n-s pixel resolution */
        """

        if px < 0:
            raise ValueError('x cannot be below 0')
        elif px is None:
            raise ValueError('x cannot be None')
        elif px > x_size:
            raise ValueError('x cannot be out of the image')
        else:
            self._px = px

        if py < 0:
            raise ValueError('y cannot be below 0')
        elif py is None:
            raise ValueError('y cannot be None')
        elif py > y_size:
            raise ValueError('y cannot be out of the image')
        else:
            self._py = py

    def save_output(self, pos, mydB, datearr, image):
        outarr=[pos[0], pos[1], self._px, self._py]
        outarr.extend(mydB)
        header=['MapX', 'MapY', 'ImageX', 'ImageY']
        header.extend(datearr)

        outname=os.path.basename(image)
        outbase=os.path.splitext(outname)[0]
        outdir=os.path.join(os.environ['HOME'],'geo_logs')
        if not os.path.exists(outdir): os.makedirs(outdir)
        outfile=os.path.join(outdir,outbase+'_ts_points.csv')
        if os.path.isfile(outfile):
            openflag='a'
        else:
            openflag='w'
        with open(outfile, openflag) as f:
            wf=csv.writer(f)                 
            if openflag=='w':
                wf.writerow(header)
            wf.writerow(outarr)
                 


def max_date_range(img):
    '''Checks for other datefiles at the same directory level, parses them and returns the min/max data range as a tuple to use as x axis scaling'''
    dfile=os.path.splitext(img)[0]+'.dates'
    dirname=os.path.dirname(dfile)
    dfiles=glob.glob('{}/*.dates'.format(dirname))
    alldates=[]
    for i in dfiles:
        idates=[ s.strip('\n') for s in open(i).readlines()]
        alldates+=idates
    alldates = [int(x) for x in list(set(alldates))]
    alldates.sort()
    alldates  = [datetime.datetime.strptime(str(x),'%Y%m%d') for x in alldates]
    if alldates != []:
        return alldates, (min(alldates),max(alldates))
    else:
        return None,(None,None)


