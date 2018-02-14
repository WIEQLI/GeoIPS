# Author:
#    Naval Research Laboratory, Marine Meteorology Division
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the NRLMMD License included with this program.  If you did not
# receive the license, see http://www.nrlmry.navy.mil/geoips for more
# information.
#
# This program is distributed WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# included license for more details.

# Python Standard Libraries
import logging
import os
from datetime import datetime,timedelta


# Installed Libraries
import numpy as np
import matplotlib
matplotlib.use('agg')
from matplotlib import cm
from matplotlib.colors import Normalize
import matplotlib.pyplot as plt
plt.switch_backend('agg')
from PIL import Image

try:
    from IPython import embed as shell
except:
    print 'Failed import IPython in geoimg/plot/rgbimg.py. If you need it, install it.'


# GeoIPS Libraries
from ..title import Title
from ..shoot import shoot
from ..mpl_utils import on_draw,parallels,meridians
from ..output_formats.metoctiff import metoctiff
import geoips.productfile as productfile
from geoips.utils.gencolormap import get_cmap
from geoips.utils.decorators import retry
from geoips.utils.memusg import print_mem_usage
from geoips.utils.log_setup import interactive_log_setup
from geoips.utils.path.productfilename import ProductFileName
from geoips.utils.satellite_info import all_sats_for_sensor
from geoips.utils.plugin_paths import paths as gpaths
from geoips.scifile.containers import DataSet,Variable
from geoips.scifile import SciFile



ColorbarBase = matplotlib.colorbar.ColorbarBase
mplrc = matplotlib.rc
rcParams = matplotlib.rcParams

log = interactive_log_setup(logging.getLogger(__name__))



plot_class_name = 'GeoImgBase'
class GeoImgBase(object):
    def __init__(self, datafile, sector, intermediate_data_output=False,sectorfile=None, product=None, title=None, cmap=None, ticks=[],
                 gridcolor=[0,0,0], coastcolor=[0,0,0]):

        log.info('Initializing %s instance. intermediate_data_output: %s' % (self.__class__.__name__,str(intermediate_data_output)))

        #Gather inputs
        self._intermediate_data_output=intermediate_data_output
        self._datafile = datafile
        self._start_datetime = datafile.start_datetime
        self._end_datetime = datafile.end_datetime
        self._sector = sector
        # Use sectorfile for pass prediction in utils.path.productfilename
        self._sectorfile = sectorfile
        self._product = product
        self._title = title
        self._ticks = ticks

        self._req_vars = product.get_required_source_vars(datafile.source_name)
        if product:
            self._logtag = sector.name+product.name
        else:
            self._logtag = sector.name

        #Gather colors
        if product is not None:
            self._gridcolor = product.gridcolor
            self._coastcolor = product.coastcolor
            if product.cmap is not None:
                self._cmap = get_cmap(product.cmap)
            else:
                self._cmap = None
            self._colorbars = product.colorbars
        else:
            self._gridcolor = gridcolor
            self._coastcolor = coastcolor
            if cmap is not None:
                self._cmap = get_cmap(cmap)
            else:
                self._cmap = None

    def register_data(self, interp_method='nearest'):
        img_dts = {}
        pname = self.product.name
        sname = self.sector.name

        #### MLS ARCHER GOES HERE ####
        #### Include TC center lat, TC center lon, intensity, frequency of product, possible addition center lat/lon as additional attributes in scifile h5 file.
        ####   I think these just go into the varinfo dictionary, and magically end up in the h5 file ?!
        ####        Name the attributes / varinfo elements as: TC_ARCHER_xxx
        ####  Include lat/lon/data arrays in scifile h5 file.
        ####  Write out the scifile into an h5 file that Archer can read into MATLAB
        ####  Archer does it's thing in MATLAB, returns a text file with possible new center lat/lon and additional metadata relating to the confidence, etc of the new lat lon.
        #### If new lat lon required, change self.sector.area_definition to reflect the new center lat lon BEFORE registering data.
        #self.datafile._finfo['TC_ARCHER_center_lon'] = self.sector.tc_info.clon
        #self.datafile._finfo['TC_ARCHER_center_lat'] = self.sector.tc_info.clat
        #self.datafile._finfo['TC_ARCHER_intensity'] = self.sector.tc_info.wind_speed
        #self.datafile._finfo['TC_ARCHER_product_frequency'] = self.product.name
        #self.datafile.write('test.h5',variables=self.req_vars,geolocation_variables=['Latitude','Longitude'])
        #shell()

        #Register data to area definition
        #Performing prior to color gun generation in order to allow
        #   for multiple data resolutions
        img_dts['start_regsingle'+sname+pname] = datetime.utcnow()

        # It looks like interp_method='gauss' does not preserve the mask 
        #   (it is still a masked array, but the 
        #   mask is False).  Using default (nearest neighbor?) preserves mask.
        #   interp_method='gauss' is needed for GOES Pyro-CB products, so 
        #   need to figure something out here. MLS
        #registered = self.datafile.register(self.sector.create_area_definition(), interp_method='gauss')
        roi = None
        if self.product.interpolation_radius_of_influence:
            roi = self.product.interpolation_radius_of_influence
        new_req_vars = []
        for varname in self.req_vars:
            new_req_vars += [varname]
            
        registered = self.datafile.register(self.sector.area_definition,
            required_vars=new_req_vars,
            roi=roi, interp_method=interp_method)
        img_dts['end_regsingle'+sname+pname] = datetime.utcnow()

        print_mem_usage(self.logtag+'gimgafterregister',True)

        #Perform day/night check
        img_dts['start_masksingle'+sname+pname] = datetime.utcnow()
        if self.product.day_ngt.lower() == 'day':
            log.info('Masking nighttime data.')
            registered.mask_night(self.product.day_ang)
        elif self.product.day_ngt.lower() == 'night':
            log.info('Masking daytime data.')
            registered.mask_day(self.product.ngt_ang)
        img_dts['end_masksingle'+sname+pname] = datetime.utcnow()
        for sttag in sorted(img_dts.keys()):
            if 'start_' in sttag:
                tag = sttag.replace('start_','')
                try:
                    log.info('process image time %-40s: '%tag+str(img_dts['end_'+tag]-img_dts['start_'+tag])+' '+socket.gethostname())
                except:
                    log.info('WARNING! No end time for '+sttag)
        return registered

    @property
    def registered_data(self):
        if not hasattr(self, '_registered_data'):
            if hasattr(self,'interp_method') and self.interp_method:
                interp_method = self.interp_method
            else:
                interp_method = 'nearest'
            self._registered_data = self.register_data(interp_method=interp_method)
            return self._registered_data
        return self._registered_data


    @property
    def start_datetime(self):
        return self._start_datetime
    @property
    def end_datetime(self):
        return self._end_datetime
    @property
    def datafile(self):
        return self._datafile
    @property
    def logtag(self):
        return self._logtag
    @property
    def req_vars(self):
        return self._req_vars
    @property
    def sector(self):
        return self._sector
    @property
    def sectorfile(self):
        return self._sectorfile
    @property
    def product(self):
        return self._product
    @property
    def title(self):
        #If we did not pass a title initially, get it from the datafile, sector, and product objects
        #Otherwise use what we passed
        if not self._title:
            if self.product.text_below_title:
                return Title.from_objects(self.datafile,self.sector,self.product,extra_lines=[self.product.text_below_title])
            else:
                return Title.from_objects(self.datafile,self.sector,self.product)
        return self._title
    @property
    def cmap(self):
        return self._cmap
    @property
    def colorbars(self):
        return self._colorbars
    @property
    def gridcolor(self):
        return self._gridcolor
    @property
    def coastcolor(self):
        return self._coastcolor
    @property
    def basemap(self):
        return self.sector.basemap
    @property
    def figure(self):
        if not hasattr(self, '_figure'):
            self._figure = None
        return self._figure
    @property
    def axes(self):
        if not hasattr(self, '_axes'):
            self._axes = None
        return self._axes
    @property
    def is_final(self):
        if not hasattr(self, '_is_final'):
            self._is_final = False
        return self._is_final

    @property
    def intermediate_data_output(self):
        if not hasattr(self, '_intermediate_data_output'):
            self._intermediate_data_output= False
        return self._intermediate_data_output

    @property
    def imagery_output(self):
        if not hasattr(self, '_imagery_output'):
            self._imagery_output= True
        return self._imagery_output

    def get_filename(self, external_product=None,merged_type=False):


        final = self.is_final
        data_output = self.intermediate_data_output

        # If we are creating final imagery - do not output data.
        # Eventually we will have to change this to support output final data products, 
        # but that is not going to work to begin with, so we'll have to readdress in the
        # future anyway.
        if self.is_final:
            data_output = False
        # fromobjects expects either geoipsfinal_product or external_product
        if external_product:
            #pdb.set_trace()
            final = False

        return ProductFileName.fromobjects(self.datafile, self.sector, self.product,
                                           geoimgobj=self, geoipsfinal_product=final,
                                            external_product=external_product, merged=merged_type,
                                            data_output=data_output,)

    def merge(self, otherimg, condition=None, other_top=False):
        #self.extend_lines(2)
        #print 'in merge, finalimg otherimg self._image'
        #shell()

        if not self.intermediate_data_output:
            #final = np.dsplit(np.flipud(self._image),4)

            if other_top:
                top_rgb = otherimg[...,:3]
                top_a = otherimg[...,3]
                bottom_rgb = np.flipud(self._image[...,:3])
                bottom_a = np.flipud(self._image[...,3])
            else:
                top_rgb = np.flipud(self._image[...,:3])
                top_a = np.flipud(self._image[...,3])
                bottom_rgb = otherimg[...,:3]
                bottom_a = otherimg[...,3]
            bottom_fulla = np.ma.dstack([bottom_a,bottom_a,bottom_a])
            top_fulla = np.ma.dstack([top_a,top_a,top_a])


            #other = np.dsplit(otherimg, 4)

            new = np.ma.empty(otherimg.shape)
            #new = np.zeros_like(self._image)


            #if other_top:
            #    out_a = other_a + final_a * (1.0 - other_a)
            #    out_rgb = (other_rgb * other_a[...,None] +  final_rgb * final_a[...,None] * (1.0-other_a[...,None]))/out_a[...,None]
            #    new[...,:3] = out_rgb
            #    new[...,3] = out_a

            #else:
            #    out_a = final_a + other_a * (1.0 - final_a)
            #    out_rgb = (final_rgb * final_a[...,None] +  other_rgb * other_a[...,None] * (1.0-final_a[...,None]))/out_a[...,None]
            #    new[...,:3] = out_rgb
            #    new[...,3] = out_a
            merge_a = top_a + bottom_a * (1.0 - top_a)
            merge_rgb = (top_rgb * top_a[...,None] +  bottom_rgb * bottom_a[...,None] * (1.0-top_a[...,None]))/merge_a[...,None]
            # rgb
            new[:,:,:3] = np.ma.where(top_fulla == 1, top_rgb, np.ma.where(top_fulla == 0, bottom_rgb, merge_rgb))
            # alp
            new[:,:,3] = np.ma.where(top_a == 1, top_a, np.ma.where(top_a == 0, bottom_a, merge_a))
            
            #for ind in range(len(final)):
            #    # Make sure we merge in the correct order
            #    if other_top:
            #        #alp = other[3]
            #        # Use pixels from other where other's image is NOT transparent
            #        # Otherwise use pixels from currently processed image (final)
            #        # other is on top in this case, so must use alpha layer from other.
            #        #new[ind] = np.ma.where(alp == 1, other[ind], final[ind])
            #        
            #    else:
            #        #alp = final[3]
            #        # Use pixels from other where currently processed image is transparent
            #        # (currently processed image - final - will be on top, so need to use it's alpha layer)
            #        #new[ind] = np.ma.where(alp > 0.4, final[ind], other[ind])
            #        #shell()
            #        new[ind][:,:,0] = np.ma.where(final_a == 1, final[ind][:,:,0], (self._image[:,:,3] * final[ind][:,:,0] +  otherimg[:,:,3] * other[ind][:,:,0] * (1.0-final[ind][:,:,0]))/alp[:,:,0])

            #self.data = np.ma.dstack(new)
            #self._image = np.flipud(np.dstack([red.data, grn.data, blu.data, alp]))
            #self._image = np.flipud(np.dstack(new))
            self._image = np.flipud(new)
        else:
            datasets = []
            for dsname in self.datafile.datasets.keys():
                #log.info('Trying to merge dataset '+dsname)
                for var in self.req_vars:
                    variables = []
                    geolocation_variables = []
                    if var in self.datafile.datasets[dsname].variables.keys():
                        #log.info('    Trying to merge variable '+var)
                        # Use np.ma to preserve mask !!!

                        # Note hstack/vstack takes a tuple!  Extra ()
                        # This may not always be hstack - has to be merged in the correct order.
                        # hstack stacks on shape[0]
                        other_width = otherimg.variables[var].shape[0]
                        self_width = self.datafile.variables[var].shape[0]
                        if other_width != self_width:
                            if other_width > self_width:
                                #If other is bigger, must pad self.                               
                                pad_width = other_width - self_width
                                pad_array = np.ma.masked_all((pad_width,self.datafile.variables[var].shape[1]))
                                data = np.ma.hstack(
                                    (otherimg.variables[var],
                                     np.ma.vstack((self.datafile.variables[var],pad_array))))
                            else:
                                #If self is bigger, must pad other.                               
                                pad_width = self_width - other_width
                                pad_array = np.ma.masked_all((pad_width,otherimg.variables[var].shape[1]))
                                data = np.ma.hstack(
                                    (np.ma.vstack((otherimg.variables[var],pad_array)),
                                     self.datafile.variables[var])
                                    )
                        else:
                            data = np.ma.hstack(
                                (otherimg.variables[var],
                                 self.datafile.variables[var])
                                )
                        varinfo = self.datafile.datasets[dsname].variables[var]._varinfo
                        variables += [Variable(var,data=data,_varinfo=varinfo,_nomask=False)]

                        gvars = ['Latitude','Longitude']+self.product.get_required_source_geolocation_vars(self.datafile.source_name)
                        for gvar in gvars:
                            #log.info('    Trying to merge geolocation variable '+gvar)
                            if other_width != self_width:
                                if other_width > self_width:
                                    data = np.ma.hstack(
                                                    (otherimg.datasets[dsname].geolocation_variables[gvar],
                                                    np.ma.vstack((self.datafile.datasets[dsname].geolocation_variables[gvar],pad_array)))
                                                    )
                                else:
                                    data = np.ma.hstack(
                                                    (np.ma.vstack((otherimg.datasets[dsname].geolocation_variables[gvar],pad_array)),
                                                    self.datafile.datasets[dsname].geolocation_variables[gvar])
                                                    )
                            else:
                                data = np.ma.hstack(
                                                (otherimg.datasets[dsname].geolocation_variables[gvar],
                                                self.datafile.datasets[dsname].geolocation_variables[gvar])
                                                )
                            varinfo = self.datafile.datasets[dsname].geolocation_variables[gvar]._varinfo
                            geolocation_variables += [Variable(gvar,data=data,_varinfo=varinfo,_nomask=False)]
                                
                vdataset = DataSet(dsname,variables=variables,copy=False)
                gdataset = DataSet(dsname,geolocation_variables=geolocation_variables,copy=False)
                self.datafile.delete_dataset(dsname)
                self.datafile.add_dataset(vdataset,copy=False)
                self.datafile.add_dataset(gdataset,copy=False)

            #log.info('In def merge')

    # 20160203 - don't appear to be mem jumps here. (could be in things
    #           it calls)
    #@profile
    def merge_images(self, merge_type='GRANULE'):
        if merge_type == 'SWATH':
            recursive = True
        else:
            recursive = False
        # We print before and after merge in process.py. Don't 
        # need in here. 
        # merge_images doesn't seem to be a problem at this point
        #print_mem_usage(self.logtag+'gimgbeforefilemerges',True)
        granules = sorted(self.geoips_product_filename.list_other_files(extra=merge_type, recursive=recursive))
        if merge_type == 'GRANULE':
            log.info('    Merging granules into: '+os.path.basename(self.geoips_product_filename.name))
        elif merge_type == 'SWATH':
            log.interactive('    Merging swaths into: '+os.path.basename(self.geoips_product_filename.name))
        for granule in granules:
            if merge_type == 'GRANULE':
                log.info('        Merging granule: '+os.path.basename(granule))
            elif merge_type == 'SWATH':
                log.interactive('        Merging swath: '+os.path.basename(granule))
            log.info('            Opening ProductFileName...')
            pfn = ProductFileName(granule)
            if pfn.processing_set():
                log.info('            Granule still being processed, skip: '+os.path.basename(granule))
                continue
            log.info('            Reading image...')
            #print_mem_usage(self.logtag+'gimgbeforereadimg ',True)
            
            currimg = self.read_image(granule)
            if pfn.datetime < self.start_datetime:
                self._start_datetime = pfn.datetime
            if pfn.datetime > self.end_datetime:
                self._end_datetime = pfn.datetime
            log.info('            Merging image...')
            #print_mem_usage(self.logtag+'gimgafterreadimg ',True)
            self.merge(currimg)
            #print_mem_usage(self.logtag+'gimgaftermerge',True)
        if merge_type == 'GRANULE':
            self.merged_type = 'SWATH'
        elif merge_type == 'SWATH':
            self.merged_type = 'FULLCOMPOSITE'
        
        if self.intermediate_data_output:
            # Remove image attribute to force reprocessing
            delattr(self,'_image')
            delattr(self,'_registered_data')
            
            self.plot()
        #print_mem_usage(self.logtag+'gimgafterfilemerges',True)
        return self

    def merge_granules(self):
        return self.merge_images(merge_type='GRANULE')
    def merge_swaths(self):
        return self.merge_images(merge_type='SWATH')

    @retry(RuntimeError,5,4)
    def read_image(self,img_file):
        if not self.intermediate_data_output:
            img = plt.imread(img_file)
            if img.min() < 0.0 or img.max() > 1.0:
                norm = Normalize(vmin=img.min(), vmax=img.max())
                img = norm(img)
            return img
        else:
            df = SciFile()
            df.import_data([img_file])
            log.info('In intermediate data file read_image')
            return df

    def find_matching_sat_files(self, source, matchall, prodname, hour_range):
        matching_sat_files = []
        # Check all satellites that have the sensor "source"
        for sat in all_sats_for_sensor(source):
            # productname is the name of the product for the current layer
            # (not the multisource product).
            if matchall:
                # Returns list if match all, single file otherwise.
                pfns = ProductFileName.nearest_file(
                    sat, source,
                    start_dt=self.datafile.start_datetime,
                    end_dt=self.datafile.end_datetime,
                    sector=self.sector,
                    productname=prodname,
                    maxtimediff=timedelta(hours=hour_range),
                    matchall=matchall,
                    intermediate_data=self.intermediate_data_output)
                if pfns:
                    matching_sat_files += pfns
            else:
                log.info('    start_dt: '+str(self.datafile.start_datetime)+
                         ' end_dt: '+str(self.datafile.end_datetime)+
                         ' hour_range: '+str(hour_range))
                pfn = ProductFileName.nearest_file(
                    sat, source,
                    start_dt=self.datafile.start_datetime,
                    end_dt=self.datafile.end_datetime,
                    sector=self.sector,
                    productname=prodname,
                    maxtimediff=timedelta(hours=hour_range),
                    matchall=matchall,
                    intermediate_data=self.intermediate_data_output)
                if pfn:
                    matching_sat_files += [pfn]
        return matching_sat_files

    def find_best_match(self, matching_sat_files):
        best_covg = None
        best_time = None
        for pfn in matching_sat_files:
            # At some point we need to determine this based on nadir lon ?
            # Some other hierarchy. Now just checking covg and time.
            nearest_dt = self.datafile.start_datetime + (self.datafile.end_datetime - self.datafile.start_datetime) / 2
            log.info('Using nearest_dt: '+str(nearest_dt))
            best_covg,best_time = pfn.get_best_match(best_covg,best_time,nearest_dt)
        # If we didn't find a matching file, loop to the next source. 
        # This will result in a product with no background.
        if not best_time:
            return None
        if best_time.coverage_to_float() == best_covg.coverage_to_float():
            best_file = best_time
        else:
            best_file = best_covg
        return best_file

    def plot_matching_files(self, matching_sat_files, plotted_self=False):
        for best_file in matching_sat_files:
            log.info('Plotting existing image '+best_file.name)
            layer_img = self.read_image(best_file.name)
            if plotted_self:
                self.merge(layer_img, other_top=True)
            else:
                self.merge(layer_img)

    def create_multisource_products(self):
        # Need to check every multisource product specified for current
        # sector to see if we need to produce.
        log.info('Checking multisource products...')
        # Need to know the "old" product name (not multisource), so we know which multisource products to run
        # (Can't just use sensorname - multiple products per sensor... And we change self.product to be the
        # multisource version, so we lose the original product)
        orig_productname = self.product.name

        if 'multisource' not in self.sector.products.keys() and not self.sector.isstitched:
            log.info('    No multisource products defined, or sector not stitched')
            return None

        if self.sector.isstitched:
            log.info('    Stitching products')
            matching_sat_files = []
            for (sourcename, proddict) in self.sector.sources.products_dict.items():
                # products_dict looks like ('abi', {'Infrared': {'testonly': 'no'}})
                # If the current product is in products_dict for current
                # source, continue
                if sourcename != self.datafile.source_name and self.product.name in proddict.keys():
                    log.info('    Checking {0} for {1}'.format(
                        sourcename, self.product.name))
                    all_sat_files = self.find_matching_sat_files(
                        source=sourcename,
                        matchall=False,
                        prodname=self.product.name,
                        hour_range=1)

                    if not all_sat_files:
                        log.info('        Found no matching files for {0} {1}'
                                 .format(sourcename, self.product.name))
                        continue
                    else:
                        best_file = self.find_best_match(all_sat_files)
                        if not best_file:
                            log.info('No '+sourcename+' files found in range, SKIPPING')
                            continue
                        else:
                            matching_sat_files += [best_file]
            self.plot_matching_files(matching_sat_files)
            if self.coverage() < self.sector.min_total_cover:
                log.info('Coverage of '+str(self.coverage())+'% less than required '+str(self.sector.min_total_cover)+'%, SKIPPING')
            else:
                self.produce_imagery(final=True)

        if 'multisource' not in self.sector.products.keys():
            log.info('    No multisource products defined')
            return None

        for prodname in self.sector.products['multisource']:
            log.info('    Trying '+prodname)
            self._product = productfile.open_product('multisource',prodname)
            # Open productfile, read out productlayers, then sort based on "order" attribute. Trust me.
            layers = sorted(productfile.open_product('multisource',prodname).productlayers.iteritems(),key=lambda x:int(x[1].order),reverse=True)
            runme = False
            # Check each layer against the source (sensor)/product of the current data file.  If the datafile source 
            #   matches any of the possiblesources for the current layer, AND the current layer has 
            #   runonreceipt set to "yes," then we know we need to create the product
            for layer in layers:
                if orig_productname == layer[0] and self.datafile.source_name_product in layer[1].possiblesources and layer[1].runonreceipt == 'yes':
                    runme=True
                    log.info('    '+self.datafile.source_name_product+' '+orig_productname+' data required for product '+prodname+', running')
            # If we found in the above loop that the multisource product needs to be produced this time,
            #   start finding and merging the layers.
            
            if runme:
                plotted_self = False
                for layer in layers:
                    matching_sat_files = []
                    # If this is not the current product that we already have in memory, need to 
                    # find the appropriate temporary file and read it in.
                    if self.datafile.source_name_product not in layer[1].possiblesources:
                        for source in layer[1].possiblesources:
                            log.info('    Checking '+source+' for '+layer[0]+' hour_range: '+str(layer[1].hour_range)+' matchall: '+str(layer[1].matchall))
                            matching_sat_files += self.find_matching_sat_files(
                                source=source,
                                matchall=layer[1].matchall,
                                prodname=layer[0],
                                hour_range=layer[1].hour_range)

                            if not matching_sat_files:
                                log.info('        Found no matching files for '+source+' '+layer[0])


                        # Loop through all files from all satellites that had matching products
                        if not layer[1].matchall:
                            best_file = self.find_best_match(matching_sat_files)
                            if not best_file:
                                log.info('Found no '+str(layer[1].possiblesources.keys())+
                                         ' files to match current layer, SKIPPING')
                                continue
                            else:
                                matching_sat_files = [best_file]

                        self.plot_matching_files(matching_sat_files,
                                                 plotted_self=plotted_self)
                    # If this layer is the current image that we have in memory, just plot it.
                    else:
                        log.info('Plotting current in-memory image')
                        plotted_self = True

                if self.coverage() < self.sector.min_total_cover:
                    log.info('Coverage of '+str(self.coverage())+'% less than required '+str(self.sector.min_total_cover)+'%, SKIPPING')
                    continue
                self.produce_imagery(final=True)
            else:
                log.info('    '+prodname+' product does not need to be produced on receipt of '+self.datafile.source_name_product+' '+orig_productname+' data, SKIPPING')

    def produce_imagery(self, final=False, clean_old_files=True, geoips_only=False,datetimes=None,datetimes_name=None):
        ''' GeoImg produce_imagery method - called from process.py 
            The actual RGBA processed image array is stored in self.image property, which is defined
                in the individual GeoImg subclasses (BasicImg, RGBImg, ExternalImg, etc). Data is 
                normalized
            GeoImg.produce_imagery is called from process.py to actually write out the image array, self.image
            either as an output image file, or as intermediate data file.
                There are 4 possible image types that are output from produce_imagery:
                    1. temporary single granule 
                        --transparent image
                        --data file
                    2. temporary granule composite (single swath)
                        --transparent image
                        --data file
                    3. temporary swath composite transparent image (multiple swaths)
                        --transparent image
                        --data file
                    4. final completely merged (if necessary) image with all labels, etc

            final=True is passed directly to produce_imagery to request final imagery
            The type of temporary image is determined based on the self.merged_type property, 
                which is set in GeoImg.merge_images ('GRANULE', 'SWATH', 'FULLCOMPOSITE')
            process.py calls GeoImg.merge_images to combine images appropriately:
                calls either "GeoImg.merge_granules()" or "GeoImg.merge_swaths()" on an existing
                    GeoImg instance (which each call GeoImg.merge_images()) to get a list of matching 
                    existing temporary files 
                reads each existing temporary file in using "GeoImg.read_image", 
                merges them into the current self.image one at a time using "GeoImg.merge", 
                then returns a new GeoImg instance with a combined RGBA 

            self.plot() is called first 
                defined within the individual subclasses (pass whether we need final imagery or not to self.plot()
                calls GeoImg._create_fig_and_ax - uses  matplotlib.pyplot.figure to create 
                    the actual image space (fig), and fig.add_axes to create the axes (ax)
                    returns fig and ax and stores as self._figure and self._axes
                Then uses self.basemap (defined from self.sector.basemap - which is in 
                    sectorfile/xml.py and uses pyresample.plot.area_def2basemap) 
                    methods to actually perform the plotting. (basemap.scatter, basemap.imshow
                If final imagery, calls GeoImg.finalize() (which uses basemap.drawmeridians,
                    basemap.drawparallels, basemap.drawcoastlines(), etc to add those to image)
                    Then uses GeoImg.sector.plot_objects to add extra stuff (circles, marks, etc)
                    to the actual figure
                Does not actually return anything, basemap just must magically give self.figure
                    it's stuff to plot ?!.
            merged_type is set to self.merged_type if it exists (SWATH or FULLCOMPOSITE'), 
                else 'GRANULE'. This is only used for naming files and log output, 
                and deleting old files if 'SWATH'
            Then uses self.figure.savefig to actually write out the imagery.  
                If final, transparent=False
                if final and produce_web, read in final image we just wrote with PIL.Image.open,
                    then write back out to produce_web path with PIL.Image.open().save
                If temporary, write with transparent=True
                
        '''
        logstr = ''
        if datetimes:
            datetimes['startplot_'+str(datetimes_name)] = datetime.utcnow()

        # Note when self.is_final is accessed, the is_final property gets set
        # to whatever _is_final is set to.  Since is_final is determined upon
        # producing imagery (not upon initializing GeoImg object. The same 
        # geoimg object will be added to before producing final imagery),
        # we set this in produce_imagery. Pretty sure explicitly setting _is_final
        # here is not best practice, some day rethink this ?
        self._is_final = final
        # If we are creating final imagery - do not output data.
        # Eventually we will have to change this to support output final data products, 
        # but that is not going to work to begin with, so we'll have to readdress in the
        # future anyway.
        if self.is_final or not self.intermediate_data_output:
            # This is what actually creates the plot. Defined in GeoImgBase subclasses
            self.plot()
        
        if datetimes:
            datetimes['endplot_'+str(datetimes_name)] = datetime.utcnow()

        if hasattr(self, 'merged_type'):
            merged_type = self.merged_type
        else:
            merged_type = 'GRANULE'
        if datetimes:
            datetimes['startwriteimagery_'+str(datetimes_name)] = datetime.utcnow()
        # Always set the GeoIPS filename.  If self.is_final, it will be a GeoIPSfinal path,
        # if not self.is_final, it will be GeoIPSTemp.
        geoips_product_filename = self.get_filename(merged_type=merged_type)
                                            
        #Creating output directory if it doesn't already exist
        geoips_product_filename.makedirs()

        self.geoips_product_filename = geoips_product_filename

        log.info('\n\n')
        #Save the image
        # If we are requesting final imagery at this point, we will produce the internal GeoIPS
        # final imagery, as well as any external final products required.
        # ie, metoctiff, TC web imagery, nexsat imagery, or lat lon h5 files.
        if self.is_final:
            #self.figure.savefig(geoips_product_filename.name, dpi=rcParams['figure.dpi'], pad_inches=0.2, transparent=False,
            #                    bbox_inches='tight', bbox_extra_artists=self.axes.texts)
            logstr = ' FINALSUCCESS: '
            log.interactive(logstr+'Writing image file: '+geoips_product_filename.name)
            geoips_product_filename.set_processing()

            self.figure.savefig(geoips_product_filename.name, dpi=rcParams['figure.dpi'], bbox_inches='tight',
                            bbox_extra_artists=self.axes.texts, pad_inches=0.2, transparent=False)
            if geoips_product_filename.coverage > 90:
                log.info('LATENCY: '+str(datetime.utcnow()-geoips_product_filename.datetime)+' '+gpaths['BOXNAME']+' '+geoips_product_filename.name)
            geoips_product_filename.move_to_final_filename()
            if clean_old_files:
                log.info("Requested to clean old files for final imagery")
                try:
                    geoips_product_filename.delete_old_files()
                except OSError, resp:
                    log.error(str(resp)+' Failed DELETING OLD FINAL IMAGES. Someone else did it for us? Skipping')

            # Loop through all necessary destinations
            for dest,dest_on in self.sector.destinations_dict.items():
                if not dest_on:
                    continue
                if geoips_only:
                    log.info('SKIPPING'+dest.upper()+' geoips_only set')
                    continue
                external_product_filename = self.get_filename(external_product=dest)
                # If dest is not defined in productfilename.py, returns None
                if not external_product_filename:
                    continue
                logstr = ' '+dest.upper()+'SUCCESS: '
                external_product_filename.makedirs()
                log.info('\n\n')
                log.info(logstr+'Writing image file: '+external_product_filename.name)
                if dest == 'metoctiff':
                    metoctiff(self,self.sector,external_product_filename.name) 
                else:
                    try:
                        finalimg = Image.open(geoips_product_filename.name)
                        external_product_filename.set_processing()
                        if os.path.splitext(external_product_filename.name)[1] in ['.jpg','.jpeg']:
                            rgbimg = finalimg.convert('RGB')
                            rgbimg.save(external_product_filename.name)
                        else:
                            finalimg.save(external_product_filename.name)
                        external_product_filename.move_to_final_filename()
                        external_product_filename.delete_old_files()
                    except IOError, resp:
                        log.warning(str(resp)+'Someone else must have already deleted this file!  Assuming the other process managed to get it to NEXSAT..')

        else:
            # alpha layer appears to have 
            logstr = ' '+merged_type+'SUCCESS: '
            if self.intermediate_data_output:
                # Note there are 3 possibilities for what we write out here - sectored, registered, or processed 
                # data.  Start with sectored - we need that because TCs move, etc.  We can figure it out later
                # if we want additional options.
                try:
                    self.datafile.write(fname=geoips_product_filename.name,
                        variables=self.req_vars,
                        geolocation_variables=['Latitude','Longitude']+self.product.get_required_source_geolocation_vars(self.datafile.source_name))
                    log.interactive(logstr+'Writing sectored data file: '+geoips_product_filename.name)
                except IOError,resp:
                    log.error(str(resp)+' Failed WRITING SECTORED DATAFILE. Someone else did it for us? Skipping')
                        
            else:
                self.figure.savefig(geoips_product_filename.name, dpi=rcParams['figure.dpi'], pad_inches=0.0, transparent=True, frameon=False)
                log.interactive(logstr+'Writing image file: '+geoips_product_filename.name)
            if merged_type == 'SWATH':
                try:
                    geoips_product_filename.delete_old_files(extra="SWATH")
                except IOError, resp:
                    log.error(str(resp)+' Failed DELETING OLD SWATH IMAGES. Someone else did it for us? Skipping')
        if datetimes:
            datetimes['endwriteimagery_'+str(datetimes_name)] = datetime.utcnow()
        #print geoips_product_filename.name
        # 20160202 This was previously commented out ? Maybe causing some memory problems?
        #              Not sure why it was commented?
        # If we are creating final imagery - do not output data.
        # Eventually we will have to change this to support output final data products, 
        # but that is not going to work to begin with, so we'll have to readdress in the
        # future anyway.
        if self.is_final or not self.intermediate_data_output:
            plt.close(self.figure)

    def coverage(self):
        '''Tests self.image to determine what percentage of the image is filled with good data.
        Test checks against alpha layer.  If alpha layer is not 1.0, bad data is assumed.
        Returns the percent coverage with good data as a float.'''
        log.debug('Testing coverage.')
        size = self.image[:,:,3].size
        good = np.where(self.image[:,:,3] == 1)
        ngood = float(len(good[0]))
        percent = float(ngood)/size * 100.0
        log.debug('Coverage: %s' % percent)
        return percent

    def _create_fig_and_ax(self):
        ''' This is insane. These things need commented so it is possible to easily adjust as needed
            In order to add additional annotations to figure, will need to adjust bm, etc here (calculate 
            the size of all the extra stuff, and adjust values accordingly up front).'''

        log.info('Creating figure and axes')
        #Gather needed rcParam constant
        dpi = rcParams['figure.dpi']            #Dots per inch
        lm = rcParams['figure.subplot.left']    #Fractional distance from left edge of figure for subplot
        rm = rcParams['figure.subplot.right']   #Fractional distance from right edge of figure for subplot
        bm = rcParams['figure.subplot.bottom']  #Fractional distance from bottom edge of figure for subplot
        tm = rcParams['figure.subplot.top']     #Fractional distance from top edge of figure for subplot

        #Gather number of lines and samples
        nl = self.sector.area_info.num_lines_calc
        ns = self.sector.area_info.num_samples_calc

        #Update font size based on number of lines
        if int(nl)/1000 != 0:
            title_fsize = 20*int(nl)/1000
        else:
            title_fsize = 20

        cbar_fsize = title_fsize
        latlon_fsize = title_fsize
        ann_fsize = title_fsize

        title_line_space = title_fsize/float(nl)             #Space needed for a single line in figure coordinates
        cbar_line_space = cbar_fsize/float(nl)             #Space needed for a single line in figure coordinates
        latlon_line_space = latlon_fsize/float(nl)
        ann_line_space = ann_fsize/float(nl)

        font = {'family': 'sans-serif',
                'weight': 'bold',
                'size': title_fsize,
               }
        mplrc('font', **font)


        #Get the figure and axes
        #NOTE: Should set facecolor='w' for final figures
        if self.is_final:

            # Figure out how many extra lines we have, for adjusting figure size.
            # I think cbar labels extend the bottom on their own, but will overwrite text below
            # if it is there. So need to calculate and adjust.
            num_text_lines_below_colorbar = 0
            if self.product.text_below_colorbars:
                #NEW LINES DO NOT APPEAR TO WORK FOR ANNOTATE AND TITLE (do work for colorbar labels ?
                # If I figure this out at some point, can calculate number of lines and adjust as needed.
                #num_text_lines_below_colorbar = len(self.product.text_below_colorbars.split('\n'))
                num_text_lines_below_colorbar = 1
            num_colorbar_label_lines = 0
            for cbind, cbarinfo in enumerate(self.colorbars):
                if hasattr(cbarinfo,'title') and cbarinfo.title and num_colorbar_label_lines < len(cbarinfo.title.split('\n')):
                    num_colorbar_label_lines = len(cbarinfo.title.split('\n'))

            # 0.020 was Jeremy's magic number ?  Just aesthetic for how tall colorbar is?
            # bottom needs to adjust based on number of lines in label
            #   Jeremy had 0.05 (aesthetics? Just so some white space below colorbar?)
            cbar_bottom = 0.05 
            cbar_height = 0.020

            # Adjust bottom margin before calculating the figure size,
            #   ONLY IF we are including annotations (extra title lines, 
            #   text after colorbars, text before colorbars.).
            #   Need to include: 
            #       text below colorbar, 
            #       height of colorbar
            #   latlon labels, colorbar labels, and title included in rcparams ?

            if num_text_lines_below_colorbar > 0:
                cbar_bottom += num_text_lines_below_colorbar*(ann_line_space)
                bm += num_text_lines_below_colorbar*(ann_line_space)
                # I think this is the correct adjustment now. Appears to work with a 
                # single line, but with more than one colorbar line, it does not 
                # adjust the bottom margins.  So manually adjust here (have to push
                # bm (bottom margin?) and cbar_bottom up so there is enough space for everything.
                if num_colorbar_label_lines > 1:
                    #print ann_line_space
                    #print num_colorbar_label_lines
                    cbar_bottom += cbar_line_space*(num_colorbar_label_lines-1)
                    bm += cbar_line_space*(num_colorbar_label_lines-1)



            log.info('Will create final figure and axes')
            #Get figure size
            xsize = (float(ns)/dpi)/(rm-lm)
            ysize = (float(nl)/dpi)/(tm-bm)
            #Create figure and axes
            fig = plt.figure(figsize=[xsize, ysize], facecolor='w')

            ax = fig.add_axes([lm, bm, rm-lm, tm-bm])
            ax.set_axis_on()


            if self.product.text_below_colorbars:
                # These can be specified in figure fraction (0,0 bottom left, 1,1 top right)
                #       axes fraction (on the actual plot)
                #       others (look up pyplot annotate)
                # 
                ax.annotate(self.product.text_below_colorbars, xy=(0, 0), 
                            #xytext=(0.5, line_space*num_text_lines_below_colorbar), 
                            xytext=(0.5, title_line_space), 
                            xycoords='figure fraction', textcoords='figure fraction',horizontalalignment='center',
                            verticalalignment='top')

            #**************************************
            #NOTE:
            #Should move this to a function I think
            #**************************************
            if len(self.colorbars) > 0:
                num_bars = float(len(self.colorbars))
                start = lm + lm/num_bars
                space = 2*lm - start
                width = (num_bars - lm*(num_bars**2 +3))/num_bars**2
                cbar_axes = []


                for cbind, cbarinfo in enumerate(self.colorbars):
                    # add_axes [left,bottom,width,height] in figure fractions
                    cbar_axes = fig.add_axes([start+cbind*(width+space), cbar_bottom, width, cbar_height])
                    cmap = get_cmap(cbarinfo.cmap)
                    cbar_norm = None
                    ticks = None
                    if len(cbarinfo.ticks) != 0:
                        ticks = cbarinfo.ticks
                        vmin = min(ticks)
                        vmax = max(ticks)
                        cbar_norm = Normalize(vmin=vmin, vmax=vmax)
                    cbar = ColorbarBase(cbar_axes, cmap=cmap, extend='both',
                                 orientation='horizontal', ticks=ticks, norm=cbar_norm)
                    if len(cbarinfo.ticklabels) != 0:
                        cbar.set_ticklabels(cbarinfo.ticklabels)
                    # MLS 20151202 This sets the font size for the color bar 
                    # tick labels. Lots of available tick params for tweaking
                    cbar.ax.tick_params(labelsize='small')
                    if cbarinfo.title is not None:
                        # MLS 20151202 size=20 sets the font size for the color 
                        # bar title. Use 60% of title font size.
                        cbar.set_label(cbarinfo.title,size=cbar_fsize)


            #Set up title
            if self.title is not None:
                xpos = 0.5                                      #Y coordinate of title in axes coordinates
                #title_space = line_space*(len(title.lines)+1)   #Space needed for title in figure coordinates
                #title_space /= sub_frac_height                  #Space needed for title in axes coordinates
                #ypos = 1 + title_space                          #Y coordinate of title in axes coordinates
                ypos = 1 + title_line_space*2                    #*2 leaves room for title and axis labels ???
                #Add title to axes
                try:
                    titlestr = self.title.to_str()
                except AttributeError:
                    titlestr = self.title
                # New lines do not seem to work for this ?!
                # Even trying "test \n test" explicitly just printed \n.
                ax.set_title(titlestr, position=[xpos, ypos])

            fig.canvas.mpl_connect('draw_event', on_draw)
        else:
            log.info('Will create temporary figure and axes.')
            #Get figure size
            xsize = float(ns)/dpi
            ysize = float(nl)/dpi
            #Create figure and axes
            fig = plt.figure(figsize=[xsize, ysize])
            ax = fig.add_axes([0,0,1,1])
            ax.set_axis_off()

        ax.set_aspect('auto')
        log.info('Done creating figure and axes.')
        return fig, ax

    def finalize(self):
        log.info('Finalizing: Adding meridians and parallels.')
        #if mark_center:
        #    x,y = bsmp(map_args['lon_0'], map_args['lat_0'])
        #    bsmp.plot(x,y, 'kx', markersize=24)
        #    bsmp.plot(x,y, 'r+', markersize=24)
 
        #Add coastlines, borders, etc
        coastcolor = self.coastcolor if max(self.coastcolor) <= 1 else [elem/255.0 for elem in self.coastcolor]

        coastlines_linewidth = 2
        countries_linewidth = 1
        states_linewidth = 0.5
        rivers_linewidth = 0
        if hasattr(self.sector.plot_info,'coastlines_linewidth'):
            coastlines_linewidth = self.sector.plot_info.coastlines_linewidth
        if hasattr(self.sector.plot_info,'countries_linewidth'):
            countries_linewidth = self.sector.plot_info.countries_linewidth
        if hasattr(self.sector.plot_info,'states_linewidth'):
            states_linewidth = self.sector.plot_info.states_linewidth
        if hasattr(self.sector.plot_info,'rivers_linewidth'):
            rivers_linewidth = self.sector.plot_info.rivers_linewidth

        if coastlines_linewidth:
            log.info('        Plotting coastlines...')
            self.basemap.drawcoastlines(ax=self.axes, linewidth=coastlines_linewidth, color=coastcolor)
        if countries_linewidth:
            log.info('        Plotting countries...')
            self.basemap.drawcountries(ax=self.axes, linewidth=countries_linewidth, color=coastcolor)
        if states_linewidth:
            log.info('        Plotting states...')
            self.basemap.drawstates(ax=self.axes, linewidth=states_linewidth, color=coastcolor)
        if rivers_linewidth:
            log.info('        Plotting rivers...')
            self.basemap.drawrivers(ax=self.axes, linewidth=rivers_linewidth, color=coastcolor)

        # Overlay a line from a set of lat/lons.
        # May look into moving this into another module, but keeping the code for now.
        #indat = '/ftp/receive/cossuth/textfile.txt'
        #lines = [line.rstrip('\n') for line in open(indat)]
        #for count, line in enumerate(lines):
        #    entries = line.split()
        #    (lat, lon) = entries
        #    #print str(count)+"   Lat: "+lat+"    Lon: "+lon
        #    if count > 0:
        #        self.basemap.plot([olon,lon], [olat,lat], 'k-', lw=3, color=(0,0.5,0), ax=self.axes)
        #    olon=lon
        #    olat=lat

        #Draw parallels and meridians
        gridcolor = self.gridcolor if max(self.gridcolor) <= 1 else [elem/255.0 for elem in self.gridcolor]
        # Some sectors just don't work well with one or more labels..
        # (Arctic gets squishy for top labels). Allow labels to be 
        # turned off in sectorfile - default to all on.
        left_label = True
        right_label = True
        bottom_label = True
        top_label = True
        grid_lines = True
        grid_lon_linewidth = 1
        grid_lon_dashes = [4,2]
        grid_linewidth = 1
        grid_dashes = [4,2] 

        if hasattr(self.sector.plot_info,'left_latlon_label'):
            left_label = self.sector.plot_info.left_latlon_label
        if hasattr(self.sector.plot_info,'right_latlon_label'):
            right_label = self.sector.plot_info.right_latlon_label
        if hasattr(self.sector.plot_info,'top_latlon_label'):
            top_label = self.sector.plot_info.top_latlon_label
        if hasattr(self.sector.plot_info,'bottom_latlon_label'):
            bottom_label = self.sector.plot_info.bottom_latlon_label
        if hasattr(self.sector.plot_info,'grid_lines'):
            grid_lines = self.sector.plot_info.grid_lines
        
        if hasattr(self.sector.plot_info,'grid_linewidth'):
            grid_linewidth= self.sector.plot_info.grid_linewidth
            # Defaults to lat/lon both equalling grid_linewidth (unless grid_lon_linewidth defined)
            grid_lon_linewidth = grid_linewidth
        if hasattr(self.sector.plot_info,'grid_lon_linewidth'):
            grid_lon_linewidth = self.sector.plot_info.grid_lon_linewidth

        if hasattr(self.sector.plot_info,'grid_dashes'):
            grid_dashes = self.sector.plot_info.grid_dashes
            # Defaults to lat/lon both equalling grid_dashes(unless grid_lon_dashes defined)
            grid_lon_dashes = grid_dashes
        if hasattr(self.sector.plot_info,'grid_lon_dashes'):
            grid_lon_dashes = self.sector.plot_info.grid_lon_dashes
        
        if grid_lines == False:
            log.info('No grid lines')
        #shell()
        elif grid_lines == True:
            self.basemap.drawparallels(parallels(self.sector), ax=self.axes, linewidth=grid_linewidth, 
                                       dashes=grid_dashes, 
                                       labels=[left_label,right_label,0,0], color=gridcolor)
            self.basemap.drawmeridians(meridians(self.sector), ax=self.axes, linewidth=grid_lon_linewidth, 
                                       dashes=grid_lon_dashes, 
                                       labels=[0,0,top_label,bottom_label], color=gridcolor)

        ### Add functionality for crosshairs plotting
        ### Need to change m1/m2 and p1/p2 to min/max sector lat/lon
        # if hasattr(self.sector.plot_info,'grid_crosshairs_lon_spacing'):
        #     if hasattr(self.sector.plot_info,'grid_crosshairs_lat_spacing'):
        #         if self.product.grid_crosshairs_color:
        #             pass
        #         else:
        #             grid_crosshairs_color=((0,0,0,1))
        #         for merid in range(m1,m2,grid_crosshairs_lon_spacing):
        #             for paral in range(p1,p2,grid_crosshairs_lat_spacing):
        #                 plt.plot(merid,paral,color=grid_crosshairs_color,marker='+')

        #If there are any objects to be plotted, then add them now
        if self.sector.plot_objects is not None:
            self.plot_objects()

    def plot_objects(self):
        '''Plot any odd objects that may have been requested in the sectorfile.'''
        objects = self.sector.plot_objects.objects
        if 'circles' in objects:
            self.plot_circles(objects['circles'])

    def plot_circles(self, circles):
        for circle in circles:
            lon0 = circle.center_lon
            lat0 = circle.center_lat
            rad = circle.radius
            color = circle.color

            lons = []
            lats = []
            for azm in range(0, 360):
                lon, lat, baz = shoot(lon0, lat0, azm, rad)
                lons.append(lon)
                lats.append(lat)
            lons.append(lons[0])
            lats.append(lats[0])
            X, Y = self.basemap(lons, lats)
            self.basemap.plot(X, Y, ax=self.axes, lw=2, color=color)