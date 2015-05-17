#!/usr/bin/env python3

from HAFASProvider import HAFASProvider
import sys

h = HAFASProvider()

stations = h.get_autocomplete_locations(sys.argv[1])
#print(stations)
for s in stations:
    print(s['name'])
