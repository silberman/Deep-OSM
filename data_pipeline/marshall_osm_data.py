'''
    1) download geojson road tiles from mapzen
    2) convert the road geographic linestrings to pixels
    2a) rasterize the roads to pixel matrices for the tiles
    2b) try using 2 pixels for the road width, then fix this with trial/error
    3b) further fix with a training layer to guess come up with the width predicitvely
    3) download corresponding MapQuest imagery for the tiles
    4) train a deep learning net with the roads as labeled data for the imagery
    5) FOSS then profits.

    geo help from: https://gist.github.com/tucotuco/1193577
'''

import os, math, urllib, sys, json
import numpy
from PIL import Image
from globalmaptiles import GlobalMercator
from geo_util import *

MAPZEN_VECTOR_TILES_API_KEY = 'vector-tiles-NsMiwBc'

class OSMDataNormalizer:  

  def __init__(self):
    data_dir = "data/"
    self.tile_size = 256
    self.make_directory(data_dir)
    self.vector_tiles_dir = "data/vector-tiles/"
    self.raster_tiles_dir = "data/raster-tiles/"
    self.make_directory(self.vector_tiles_dir)
    self.make_directory(self.raster_tiles_dir)

    self.current_tile = None

  def make_directory(self, new_dir):
    '''
       try to make a new directory
    '''
    try:
      os.mkdir(new_dir);
    except:
      pass

  def default_bounds_to_analyze(self):
    '''
        analyze a small chunk around Yosemite Village, by default
    '''
    yosemite_village_bb = BoundingBox()
    yosemite_village_bb.northeast.lat = 37.81385
    yosemite_village_bb.northeast.lon = -119.48559
    yosemite_village_bb.southwest.lat = 37.66724
    yosemite_village_bb.southwest.lon = -119.72454
    return yosemite_village_bb

  def default_zoom(self):
    '''
        analyze tiles at TMS zoom level 12, by default
    '''
    return 12

  def default_vector_tile_base_url(self):
    ''' 
        the default server to get vector data to train on
    '''
    return 'http://vector.mapzen.com/osm/'

  def default_raster_tile_base_url(self):
    ''' 
        the default server to get satellite imagery to analyze
    '''
    return 'http://otile2.mqcdn.com/tiles/1.0.0/sat/'

  def download_rasters(self):
    ''' 
        download raster satellite tiles for the region to be analyzed
    '''
    bounding_box = self.default_bounds_to_analyze()
    zoom = self.default_zoom()
    for tile in self.tiles_for_bounding_box(bounding_box, zoom):
      self.download_tile(self.default_raster_tile_base_url(), 
                         'jpg', 
                         self.raster_tiles_dir, 
                         tile)
    
  def download_geojson(self):
    ''' 
        download geojson tiles for the region to be analyzed
    '''
    bounding_box = self.default_bounds_to_analyze()
    zoom = self.default_zoom()

    for tile in self.tiles_for_bounding_box(bounding_box, zoom):
      self.download_tile(self.default_vector_tile_base_url(), 
                         'json', 
                         self.vector_tiles_dir, 
                         tile,
                         suffix = '?api_key={}'.format(MAPZEN_VECTOR_TILES_API_KEY),
                         layers = 'roads')

  def tiles_for_bounding_box(self, bounding_box, zoom):
    '''
        returns a list of MeractorTiles that intersect the bounding_box
        at the given zoom
    '''
    tile_array = []
    ne_tile = self.tile_with_coordinates_and_zoom(bounding_box.northeast,
                                                  zoom)
    sw_tile = self.tile_with_coordinates_and_zoom(bounding_box.southwest,
                                                  zoom)
      
    min_x = min(ne_tile.x, sw_tile.x)
    min_y = min(ne_tile.y, sw_tile.y)
    max_y = max(ne_tile.y, sw_tile.y)
    max_x = max(ne_tile.x, sw_tile.x)
    for y in range(min_y, max_y):
      for x in range(min_x, max_x):
        new_tile = MercatorTile()
        new_tile.x = x
        new_tile.y = y
        new_tile.z = zoom
        tile_array.append(new_tile)
    return tile_array

  def tile_with_coordinates_and_zoom(self, coordinates, zoom):
    '''
        returns a MeractorTile for the given coordinates and zoom
    '''
    scale = (1<<zoom);
    normalized_point = self.normalize_pixel_coords(coordinates)
    return MercatorTile(int(normalized_point.lat * scale), 
                        int(normalized_point.lon * scale), 
                        int(zoom)
                       )

  def normalize_pixel_coords(self, coord):
    '''
        convert lat lon to TMS meters
    '''
    if coord.lon > 180:
      coord.lon -= 360
    coord.lon /= 360.0
    coord.lon += 0.5
    coord.lat = 0.5 - ((math.log(math.tan((math.pi/4) + ((0.5 * math.pi *coord.lat) / 180.0))) / math.pi) / 2.0)
    return coord    

  def download_tile(self, base_url, format, directory, tile, suffix='', layers=None):
    '''
        download a map tile from a TMS server
    '''
    url = self.url_for_tile(base_url, format, tile, suffix, layers)
    print 'DOWNLOADING: ' + url
    z_dir = directory + str(tile.z)
    y_dir = z_dir + "/" + str(tile.y)
    self.make_directory(z_dir)
    self.make_directory(y_dir)
    filename = '{}.{}'.format(tile.x,format)
    download_path = y_dir + "/" + filename
    urllib.urlretrieve (url, download_path)

  def url_for_tile(self, base_url, format, tile, suffix='', layers=None):
    '''
        compose a URL for a TMS server
    '''
    filename = '{}.{}'.format(tile.x,format)
    url = base_url 
    if layers:
      url += '{}/'.format(layers)
    url = url + '{}/{}/{}{}'.format(tile.z,tile.y,filename,suffix)
    return url 

  def process_geojson(self):
    '''
        convert geojson vector tiles to 256 x 256 matrices
        matrix is 1 if the pixel has road on it, 0 if not
    '''
    rootdir = self.vector_tiles_dir
    self.gm = GlobalMercator()
    for folder, subs, files in os.walk(rootdir):
      for filename in files:
        with open(os.path.join(folder, filename), 'r') as src:
          linestrings = self.linestrings_for_vector_tile(src)
        tile_matrix = self.empty_tile_matrix()
        tile = self.tile_for_folder_and_filename(folder, filename, self.vector_tiles_dir)
        # keep track of current tile to clip off off-tile points in linestrings
        self.current_tile = tile
        for linestring in linestrings:
          tile_matrix = self.add_linestring_to_matrix(linestring, tile, tile_matrix)
        # self.print_matrix(tile_matrix)

  def process_rasters(self):
    '''
        convert raster satellite tiles to 256 x 256 matrices
        floats represent some color info about each pixel
    '''
    rootdir = self.raster_tiles_dir
    self.gm = GlobalMercator()
    for folder, subs, files in os.walk(rootdir):
      for filename in files:
        tile = self.tile_for_folder_and_filename(folder, filename, self.raster_tiles_dir)
        self.jpeg_to_rgb_matrix(Image.open(os.path.join(folder, filename)))

  # http://code.activestate.com/recipes/577591-conversion-of-pil-image-and-numpy-array/
  def jpeg_to_rgb_matrix(self, img):
    return numpy.array(img.getdata(),
                    numpy.uint8).reshape(img.size[1], img.size[0], 3)

  def tile_for_folder_and_filename(self, folder, filename, directory):
    '''
        the MeractorTile given a path to a file on disk
    '''
    dir_string = folder.split(directory)
    z, x = dir_string[1].split('/')
    y = filename.split('.')[0]
    return MercatorTile(int(x), int(y), int(z))

  def linestrings_for_vector_tile(self, file_data):
    '''
        flatten linestrings and multilinestrings in a geojson tile
        to a list of linestrings
    '''
    features = json.loads(file_data.read())['features']
    linestrings = []          
    count = 0
    for f in features:
      if f['geometry']['type'] == 'LineString':
        linestring = f['geometry']['coordinates']
        linestrings.append(linestring)   
      if f['geometry']['type'] == 'MultiLineString':
        for ls in f['geometry']['coordinates']:
          linestrings.append(ls)   
    return linestrings

  def add_linestring_to_matrix(self, linestring, tile, matrix):
    '''
        add a pixel linestring to the matrix for a given tile
    '''
    line_matrix = self.pixel_matrix_for_linestring(linestring, tile)
    for x in range(0,self.tile_size):
      for y in range(0,self.tile_size):
        if line_matrix[x][y]:
          matrix[x][y] = line_matrix[x][y] 
    return matrix

  def print_matrix(self, matrix):
    '''
        print an ascii matrix in cosole
    '''
    for row in numpy.rot90(numpy.fliplr(matrix)):
      row_str = ''
      for cell in row:
        row_str += str(cell)
      print row_str

  def empty_tile_matrix(self):
    ''' 
        initialize the array to all zeroes
    '''
    tile_matrix = []    
    for x in range(0,self.tile_size):
      tile_matrix.append([])
      for y in range(0,self.tile_size):
        tile_matrix[x].append(0)     
    return tile_matrix

  def pixel_matrix_for_linestring(self, linestring, tile):
    '''
       set pixel_matrix to 1 for every point between all points on the line string
    '''

    line_matrix = self.empty_tile_matrix()
    zoom = tile.z

    count = 0
    for current_point in linestring:
      if count == len(linestring) - 1:
        break
      next_point = linestring[count+1]
      current_point_obj = Coordinate(current_point[1], current_point[0])
      next_point_obj = Coordinate(next_point[1], next_point[0])
      
      start_pixel = self.fromLatLngToPoint(current_point_obj.lat,
                                      current_point_obj.lon, zoom)      
      end_pixel = self.fromLatLngToPoint(next_point_obj.lat,
                                    next_point_obj.lon, zoom)
      pixels = self.pixels_between(start_pixel, end_pixel)
      for p in pixels:
        line_matrix[p.x][p.y] = 1
      count += 1

    return line_matrix

  def fromLatLngToPoint(self, lat, lng, zoom):
    '''
       convert a lat/lng/zoom to a pixel on a self.tile_size sized tile
    '''
  
    tile = self.gm.GoogleTileFromLatLng(lat, lng, zoom)
    
    tile_x_offset =  (tile[0] - self.current_tile.x) * self.tile_size
    tile_y_offset = (tile[1] - self.current_tile.y) * self.tile_size
    
    # http://stackoverflow.com/a/17419232/108512
    _pixelOrigin = Pixel()
    _pixelOrigin.x = self.tile_size / 2.0
    _pixelOrigin.y = self.tile_size / 2.0
    _pixelsPerLonDegree = self.tile_size / 360.0
    _pixelsPerLonRadian = self.tile_size / (2 * math.pi)

    point = Pixel()
    point.x = _pixelOrigin.x + lng * _pixelsPerLonDegree

    # Truncating to 0.9999 effectively limits latitude to 89.189. This is
    # about a third of a tile past the edge of the world tile.
    siny = self.bound(math.sin(self.degreesToRadians(lat)), -0.9999,0.9999)
    point.y = _pixelOrigin.y + 0.5 * math.log((1 + siny) / (1 - siny)) *- _pixelsPerLonRadian

    num_tiles = 1 << zoom
    point.x = int(point.x * num_tiles) + tile_x_offset - self.current_tile.x* self.tile_size
    point.y = int(point.y * num_tiles) + tile_y_offset - self.current_tile.y* self.tile_size
    return point

  def degreesToRadians(self, deg):
    '''
        return radians given degrees
    ''' 
    return deg * (math.pi / 180)
    
  def bound(self, val, valMin, valMax):
    '''
        used to cap the TMS bounding box to clip the poles
    ''' 
    res = 0
    res = max(val, valMin);
    res = min(val, valMax);
    return res;
    
  def pixels_between(self, start_pixel, end_pixel):
    '''
        return a list of pixels along the ling from 
        start_pixel to end_pixel
    ''' 
    pixels = []
    if end_pixel.x - start_pixel.x == 0:
      for y in range(min(end_pixel.y, start_pixel.y),
                     max(end_pixel.y, start_pixel.y)):
        p = Pixel()
        p.x = end_pixel.x
        p.y = y
        if self.pixel_is_on_tile(p):
          pixels.append(p) 
      return pixels
      
    slope = (end_pixel.y - start_pixel.y)/float(end_pixel.x - start_pixel.x)
    offset = end_pixel.y - slope*end_pixel.x

    num_points = self.tile_size
    i = 0
    while i < num_points:
      p = Pixel()
      floatx = start_pixel.x + (end_pixel.x - start_pixel.x) * i / float(num_points)
      p.x = int(floatx)
      p.y = int(offset + slope * floatx)
      i += 1
    
      if self.pixel_is_on_tile(p):
        pixels.append(p) 

    return pixels

  def pixel_is_on_tile(self, p):
    '''
        return true of p.x and p.y are >= 0 and < self.tile_size
    '''
    if (p.x >= 0 and p.x < self.tile_size and p.y >= 0 and p.y < self.tile_size):
      return True
    return False

odn = OSMDataNormalizer()
#odn.download_geojson()
#odn.process_geojson()
#odn.download_rasters()
odn.process_rasters()