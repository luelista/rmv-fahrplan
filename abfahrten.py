#!/usr/bin/env python3

from HAFASProvider import HAFASProvider
import json
from datetime import datetime
import sys
import getopt

optlist, args = getopt.getopt(sys.argv[1:], 'q:f:')
options = {}
for key,val in optlist:
    options[key] = val

if not '-q' in options:
    options['-q'] = "DA Willy-Brand-Platz"

h = HAFASProvider()

res,conns = h.get_stboard(query=options['-q'])

i = 0
for x in conns:
  #print(datetime.fromtimestamp(x['time'], tzinfo=tz).strftime('%H:%M:%S') + '\t' +  x['name']['normal'] + "\t" + x['direction']['normal'])
  print(x['time'] + '\t' +  x['name']['normal'] + "\t" + x['direction']['normal'])
  if i > 20: break
  i += 1


#print (json.dumps(conns, sort_keys=True,
#                  indent=4, separators=(',', ': ')))

