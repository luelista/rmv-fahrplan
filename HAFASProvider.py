#!/usr/bin/python3
import urllib.request
import urllib.parse
import time
import calendar
import copy
from lxml import etree


class HAFASProvider:
    stboard_uri = "https://www.rmv.de/auskunft/bin/jp/stboard.exe/"
    stboard_dtd = "http://www.rmv.de/xml/hafasXMLStationBoard.dtd"  # unused, HTTPS => 503
    stboard_params = {}
    stboard_headers = {}

    stboard_lang = 'd'  # language (d = german, e = english, ...)
    stboard_type = 'n'  # UNK type
    stboard_station_suggestions = '?'  # suggestions for ambigious station names (!=no, ?=yes)
                                       # breaks RMVs stboard..., not sure why

    tz = 'CET'  # interprate time with this timezone

    def __init__(self):
        # request params defaults
        #self.stboard_params['L'] = 'vs_rmv.vs_sq'  # Layout (affects web form output)
        #self.stboard_params['L'] = 'vs_java3' # seems to be a generic layout available in every installation
        self.stboard_params['selectDate'] = 'today'  # Day (yesterday, today)
        self.stboard_params['time'] = 'actual'  # Time (use 'actual' for now or 'HHMM')
        self.stboard_params['input'] = '{station}'  # Search Query
        self.stboard_params['disableEquivs'] = 'yes'  # Don't use nearby stations
        self.stboard_params['maxJourneys'] = '50'  # Maximal number of results
        self.stboard_params['boardType'] = 'dep'  # Departure / Arrival
        self.stboard_params['productsFilter'] = '11111111111'  # Means of Transport (skip or 11111111111 for all)
        self.stboard_params['maxStops'] = 10  # UNK
        self.stboard_params['rt'] = 1  # Enable Realtime-Data
        self.stboard_params['start'] = 'yes'  # Start Query or Webform
        # UNUSED / UNTESTED AT THIS POINT
        # inputTripelId (sic!)  Direct Reference to a Station as returned by the undocumented station search
        # inputRef              Refer to station by <stationName>#<externalId>
        self.stboard_params['output'] = 'xml'  # Output Format (auto fallback to some html website)

        # http headers to send with each request
        self.stboard_headers['Host'] = 'www.rmv.de'
        self.stboard_headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64; rv:27.0) Gecko/20100101 Firefox/27.0'

    @staticmethod
    def __handle_station(leaf):
        info = {}
        for element in leaf:
            if element.tag == 'ExternalId':
                info['external_id'] = element.text
                info['pooluic'] = element.get('pooluic')
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
                parseable_datetime = '{date} {time} {tz}'.format(date=start_date, time=time_attr.text, tz=HAFASProvider.tz)
                timestamp = int(calendar.timegm(time.strptime(parseable_datetime, '%Y%m%d %H:%M %Z')))

                # if HH of the startT element is larger than the current element we
                # experienced a daychange, add 60*60*24=86400 to timestamp
                if int(time_attr.text[:2]) < int(start_time[:2]):
                    timestamp += 86400
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
                x = attr.get('x')
                lon = float('{int}.{frac}'.format(int=x[:2], frac=x[2:])) if len(x) == 8 else float('{int}.{frac}'.format(int=x[:1], frac=x[1:]))
                y = attr.get('y')
                lat = float('{int}.{frac}'.format(int=y[:2], frac=y[2:])) if len(y) == 8 else float('{int}.{frac}'.format(int=y[:1], frac=y[1:]))
                type = attr.get('type')

                # get generic station information
                for st in attr:  # Station
                    stop = HAFASProvider.__handle_station(st)

                    # append location data to station_info
                    location = {'lat': lat, 'lon': lon, 'type': type}
                    stop['location'] = location

            elif attr.tag == 'Dep' or attr.tag == 'Arr':
                stop['time'], stop['delay'], stop['platform'] = HAFASProvider.__handle_departure_or_arrival(attr, start_date, start_time)

            else:
                print('Unhandled BasicStop child ({tag}) found.'.format(tag=attr.tag))
        return index, stop


    def get_stboard(self, station, when='actual', discard_nearby='yes', max_results='50', products='11111111111',
                    type='dep'):
        '''
        returns a tuple with (station_info, connections)
        '''
        # copy default params and update with function params
        params = copy.deepcopy(self.stboard_params)
        params['input'] = station
        params['time'] = when
        params['disableEquivs'] = discard_nearby
        params['maxJourneys'] = max_results
        params['productsFilter'] = products
        params['boardType'] = type
        qp = urllib.parse.urlencode(params)

        # request
        req_uri = "{base_uri}{lang}{type}{suggestions}{query_params}".format(base_uri=self.stboard_uri, \
            lang=self.stboard_lang, type=self.stboard_type, suggestions=self.stboard_station_suggestions, \
            query_params=qp)
        print(req_uri)
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
        origin_station_info = self.__handle_station(origin_station)

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
        for header, value in self.stboard_headers.items():
            request.add_header(header, value)
