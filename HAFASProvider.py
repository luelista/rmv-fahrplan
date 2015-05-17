#!/usr/bin/python3
import json
import urllib.request
import urllib.parse
import time
import calendar
import copy
from lxml import etree


class HAFASProvider:
    __base_uri = 'https://www.rmv.de/auskunft/bin/jp/'

    __query_path = 'query.exe/'
    __getstop_path = 'ajax-getstop.exe/'
    __stboard_path = 'stboard.exe/'  # DTD http://www.rmv.de/xml/hafasXMLStationBoard.dtd

    __lang = 'd'
    __type = 'n'
    __with_suggestions = '?'  # ? = yes, ! = no

    __http_headers = {}

    __tz = 'CET'  # interprate time with this timezone

    def __init__(self):
        # http headers to send with each request

        # parse base url for Host-Header
        url = urllib.parse.urlparse(self.__base_uri)
        self.__http_headers['Host'] = url.netloc

        # disguise as a browser
        self.__http_headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64; rv:27.0) Gecko/20100101 Firefox/27.0'

    @staticmethod
    def __handle_station(leaf):
        info = {}
        for element in leaf:
            if element.tag == 'ExternalId':
                info['external_id'] = int(element.text)
                info['pooluic'] = int(element.get('pooluic'))
            elif element.tag == 'HafasName':
                text_elem = element.find('Text')
                info['name'] = text_elem.text
            else:
                print('Unhandled Station Data ({tag}) available.'.format(tag=element.tag))

        return info

    @staticmethod
    def __handle_departure_or_arrival(leaf, start_date, start_time):
        # Note: MainStop/BasicStop will either be arrival or depature, every other PassList/BasicStop will be arrival
        timestamp = 0  # assume zero delay if delay attribute is missing
        delay = 0
        platform = -1
        for time_attr in list(leaf):
            if time_attr.tag == 'Time':
                # time is formatted as HH:MM
                timestamp = time_attr.text
                #parseable_datetime = '{date} {time} {tz}'.format(date=start_date, time=time_attr.text, tz=HAFASProvider.__tz)
                #timestamp = int(calendar.timegm(time.strptime(parseable_datetime, '%Y%m%d %H:%M %Z')))

                # if HH of the startT element is larger than the current element we
                # experienced a daychange, add 60*60*24=86400 to timestamp
                #if int(time_attr.text[:2]) < int(start_time[:2]):
                #    timestamp += 86400
            elif time_attr.tag == 'Delay':
                # convert delay to seconds, for easier calculations with unix timestamps
                delay = 60 * int(time_attr.text)
            elif time_attr.tag == 'Platform':
                # platform where the connection departs from ... strangely enough this resides under time.
                platform = time_attr.text
            else:
                print('Unhandled time attribute ({tag} found.'.format(tag=time_attr.tag))

        return timestamp, delay, platform

    @staticmethod
    def __handle_basic_stop(leaf, start_date, start_time):
        # BasicStop
        index = int(leaf.get('index'))
        stop = {}
        for attr in leaf:  # BasicStop Attributes
            if attr.tag == 'Location':
                # parse location information
                x = int(attr.get('x'))
                y = int(attr.get('y'))
                lon = x / 1000000
                lat = y / 1000000
                type = attr.get('type')

                # get generic station information
                for st in attr:  # Station
                    stop = HAFASProvider.__handle_station(st)

                    # append location data to station_info
                    location = {'lat': lat, 'lon': lon, 'x': x, 'y': y, 'type': type}
                    stop['location'] = location

            elif attr.tag == 'Dep' or attr.tag == 'Arr':
                stop['time'], stop['delay'], stop['platform'] = HAFASProvider.__handle_departure_or_arrival(attr, start_date, start_time)

            else:
                print('Unhandled BasicStop child ({tag}) found.'.format(tag=attr.tag))
        return index, stop


    def get_stboard(self, query, when='actual', discard_nearby='yes', max_results='50', products='11111111111',
                    type='dep'):
        '''
        returns a tuple with (station_info, connections)
        '''

        # request params defaults
        query_param = {}
        #query_param['L'] = 'vs_rmv.vs_sq'  # Layout (affects web form output)
        #query_param['L'] = 'vs_java3' # seems to be a generic layout available in every installation
        query_param['selectDate'] = 'today'  # Day (yesterday, today)
        query_param['time'] = when # Time (use 'actual' for now or 'HHMM')
        query_param['input'] = query  # Search Query (can be a String or Integer (ExternalId))
        query_param['disableEquivs'] = discard_nearby # Don't use nearby stations
        query_param['maxJourneys'] = max_results # Maximal number of results
        query_param['boardType'] = type # Departure / Arrival
        query_param['productsFilter'] = products # Means of Transport (skip or 11111111111 for all)
        query_param['maxStops'] = 10  # max amount of intermediate stops for each connection
        query_param['rt'] = 1  # Enable Realtime-Data
        query_param['start'] = 'yes'  # Start Query or Webform
        # UNUSED / UNTESTED AT THIS POINT
        # inputTripelId (sic!)  Direct Reference to a Station as returned by the undocumented station search
        # inputRef              Refer to station by <stationName>#<externalId>
        query_param['output'] = 'xml'  # Output Format (auto fallback to some html website)

        qp = urllib.parse.urlencode(query_param)

        # request
        req_uri = "{base_uri}{binary_path}{lang}{type}{suggestions}{query_params}".format(base_uri=self.__base_uri, \
            lang=self.__lang, type=self.__type, suggestions=self.__with_suggestions, \
            query_params=qp, binary_path=self.__stboard_path)
        #print(req_uri)
        req = urllib.request.Request(req_uri)
        self.__add_http_headers(req)
        res = urllib.request.urlopen(req)
        data = res.read()

        # xml handling
        root = etree.fromstring(data)

        # get start time to calculate the day and detect daychanges
        start_date = root.find("SBRes/SBReq/StartT").get("date")
        start_time = root.find("SBRes/SBReq/StartT").get("time")

        # station that hafas selected
        origin_station = list(root.find("SBRes/SBReq/Start/Station"))
        try:
            origin_station_info = self.__handle_station(origin_station)
        except TypeError:
            raise StationNotFoundException

        connections = []
        for journey in root.findall('SBRes/JourneyList/Journey'):  # Journey
            # connection-level
            conn = {'train_id': journey.get('trainId')}
            stops = {}

            for elem in list(journey):
                if elem.tag == 'JourneyAttributeList':
                    # JourneyAttributeList
                    for journey_attr in list(elem):  # JourneyAttribute
                        # attribute validity
                        valid_from = journey_attr.get('from')
                        valid_to = journey_attr.get('to')
                        for attr in list(journey_attr):  # Attribute
                            # attribute description and priority
                            priority = attr.get('priority')
                            type = attr.get('type').lower()
                            conn[type] = {}
                            for attr_type in list(attr):  # AttributeCode, AttributeVariant
                                # the actual data stuff,
                                # AttributeCode is usually a numeric representation of AttributeVariant
                                if attr_type.tag == 'AttributeCode':
                                    variant_code = attr_type.text
                                    conn[type]['code'] = variant_code
                                elif attr_type.tag == 'AttributeVariant':
                                    variant_type = attr_type.get('type').lower()
                                    for text_field in attr_type:
                                        value = text_field.text
                                        conn[type][variant_type] = value
                                else:
                                    print('Unhandled attribute type ({tag}) found.'.format(tag=attr_type.tag))

                elif elem.tag == 'MainStop':
                    # MainStop
                    # departure station, will match selected station, but may be different if disableEquivs=no
                    for stop in list(elem):
                        index, stop = self.__handle_basic_stop(stop, start_date, start_time)

                        # Directly write back time/delay to connection, because this is the MainStop/BasicStop,
                        # do not do this in PassList/BasicStop
                        conn['time'] = stop['time']
                        conn['delay'] = stop['delay']

                        # Also write back location to origin station if external_id and pooluic match
                        if stop['external_id'] == origin_station_info['external_id'] and \
                           stop['pooluic'] == origin_station_info['pooluic']:
                            origin_station_info['location'] = stop['location']

                        stops[index] = stop

                elif elem.tag == 'Product':
                    # Product
                    # information is redundant with JourneyAttribute type='name'
                    pass

                elif elem.tag == 'PassList':
                    # PassList
                    for stop in list(elem):
                        index, stop = self.__handle_basic_stop(stop, start_date, start_time)
                        stops[index] = stop

                elif elem.tag == 'InfoTextList':
                    # InfoTextList
                    # some additional commentary on the route
                    conn['infotext'] = []
                    for infotext in elem:
                        info = {'title': infotext.get('text'), 'text': infotext.get('textL')}
                        conn['infotext'].append(info)

                else:
                    print('Unhandled Journey child ({tag}) found.'.format(tag=elem.tag))

            conn['stops'] = stops
            connections.append(conn)

        return origin_station_info, connections

    def __add_http_headers(self, request):
        for header, value in self.__http_headers.items():
            request.add_header(header, value)

    def get_nearby_stations(self, x, y, max=25, dist=5000):
        # x = lon / 1000000, y = lat / 10000000
        #print("X: {} Y: {}".format(x, y))

        # parameters
        query_param = {}
        query_param['performLocating'] = 2
        query_param['tpl'] = 'stop2json'
        query_param['look_maxno'] = max
        query_param['look_maxdist'] = dist
        query_param['look_nv'] = 'get_stopweight|yes'
        query_param['look_x'] = x
        query_param['look_y'] = y
        qp = urllib.parse.urlencode(query_param)

        # request
        req_uri = "{base_uri}{binary_path}{lang}{type}y{suggestions}{query_params}".format(base_uri=self.__base_uri, \
            lang=self.__lang, type=self.__type, suggestions=self.__with_suggestions, \
            query_params=qp, binary_path=self.__query_path)
        print(req_uri)
        req = urllib.request.Request(req_uri)
        self.__add_http_headers(req)
        res = urllib.request.urlopen(req)
        data = res.read()
        data = data.decode('utf-8')

        root = json.loads(data)
        stops = []
        for stop in root['stops']:
            stops.append({'name': stop['name'],
                          'external_id': int(stop['extId']),
                          'pooluic': int(stop['puic']),
                          'lat': int(stop['y']) / 1000000,
                          'lon': int(stop['x']) / 1000000,
                          'dist': int(stop['dist']),
                          'weight': int(stop['stopweight']),
                          'products': int(stop['prodclass'])})

        return stops

    def get_autocomplete_locations(self, query, max=25):
        # parameters
        query_param = {}
        query_param['getstop'] = 1
        query_param['REQ0JourneyStopsS0A'] = max
        query_param['REQ0JourneyStopsS0G'] = query
        qp = urllib.parse.urlencode(query_param)

        # request
        req_uri = "{base_uri}{binary_path}{lang}{type}{suggestions}{query_params}".format(base_uri=self.__base_uri, \
            lang=self.__lang, type=self.__type, suggestions=self.__with_suggestions, \
            query_params=qp, binary_path=self.__getstop_path)
        print(req_uri)
        req = urllib.request.Request(req_uri)
        self.__add_http_headers(req)
        res = urllib.request.urlopen(req)
        data = res.read()
        data = data.decode('utf-8')

        begin = data.find('{')
        end = data.rfind('}')
        root = json.loads(data[begin:end+1])

        stops = []
        for stop in root['suggestions']:
            try:
                stops.append({'name': stop['value'],
                              'external_id': stop['extId'],
                              'lat': int(stop['ycoord']) / 1000 if stop['ycoord'].isdigit() else None,
                              'lon': int(stop['xcoord']) / 1000 if stop['xcoord'].isdigit() else None,
                              'weight': int(stop['weight']),
                              'products': stop['prodClass'],
                              'type': stop['type']})
            except KeyError as e:
                print("Caught KeyError in get_autocomplete_location: {}".format(e))

        return sorted(stops, key = lambda stop: stop['weight'], reverse=True)


class HAFASException(Exception):
    pass

class StationNotFoundException(HAFASException):
    pass

