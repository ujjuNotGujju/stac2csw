#!/usr/bin/env bash
# Demo requests for every CSW operation. Usage: scripts/demo_requests.sh [http://localhost:8000/csw]
CSW="${1:-http://localhost:8000/csw}"
hr(){ printf '\n=== %s\n' "$1"; }
show(){ python3 -c "import sys,re;x=sys.stdin.read();print(re.sub(r'\s+',' ',x)[:${1:-700}])"; }

hr "1. GetCapabilities (KVP)"
curl -s "$CSW?service=CSW&version=2.0.2&request=GetCapabilities" | grep -o '<ows:Operation name="[A-Za-z]*"'

hr "2. DescribeRecord (KVP)"
curl -s "$CSW?service=CSW&version=2.0.2&request=DescribeRecord&typeName=csw:Record&outputFormat=application/xml" | show 300

hr "3. GetRecords - free text, CQL (KVP)"
curl -s -G "$CSW" --data-urlencode service=CSW --data-urlencode version=2.0.2 --data-urlencode request=GetRecords \
  --data-urlencode typeNames=csw:Record --data-urlencode resultType=results --data-urlencode elementSetName=summary \
  --data-urlencode constraintLanguage=CQL_TEXT --data-urlencode "constraint=csw:AnyText like '%CartoDEM%'" --data-urlencode maxRecords=3 \
  | grep -o '<csw:SearchResults[^>]*>\|<dc:identifier>[^<]*'

hr "4. GetRecords - bounding box, OGC Filter (XML POST)"
curl -s -X POST -H 'Content-Type: application/xml' "$CSW" -d '<?xml version="1.0"?>
<csw:GetRecords xmlns:csw="http://www.opengis.net/cat/csw/2.0.2" xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:gml="http://www.opengis.net/gml" service="CSW" version="2.0.2" resultType="results" maxRecords="5">
  <csw:Query typeNames="csw:Record"><csw:ElementSetName>brief</csw:ElementSetName>
    <csw:Constraint version="1.1.0"><ogc:Filter><ogc:BBOX>
      <ogc:PropertyName>ows:BoundingBox</ogc:PropertyName>
      <gml:Envelope srsName="urn:x-ogc:def:crs:EPSG:6.11:4326">
        <gml:lowerCorner>10 70</gml:lowerCorner><gml:upperCorner>20 80</gml:upperCorner>
      </gml:Envelope></ogc:BBOX></ogc:Filter></csw:Constraint></csw:Query></csw:GetRecords>' \
  | grep -o '<csw:SearchResults[^>]*>\|<dc:identifier>[^<]*'

hr "5. GetRecords - text AND bbox, ISO 19139 output (XML POST)"
curl -s -X POST -H 'Content-Type: application/xml' "$CSW" -d '<?xml version="1.0"?>
<csw:GetRecords xmlns:csw="http://www.opengis.net/cat/csw/2.0.2" xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:gml="http://www.opengis.net/gml" xmlns:gmd="http://www.isotc211.org/2005/gmd" service="CSW" version="2.0.2"
  resultType="results" maxRecords="3" outputSchema="http://www.isotc211.org/2005/gmd">
  <csw:Query typeNames="gmd:MD_Metadata"><csw:ElementSetName>brief</csw:ElementSetName>
    <csw:Constraint version="1.1.0"><ogc:Filter><ogc:And>
      <ogc:PropertyIsLike wildCard="%" singleChar="_" escapeChar="\">
        <ogc:PropertyName>csw:AnyText</ogc:PropertyName><ogc:Literal>%AIS%</ogc:Literal></ogc:PropertyIsLike>
      <ogc:BBOX><ogc:PropertyName>ows:BoundingBox</ogc:PropertyName>
        <gml:Envelope srsName="urn:x-ogc:def:crs:EPSG:6.11:4326">
          <gml:lowerCorner>10 70</gml:lowerCorner><gml:upperCorner>20 80</gml:upperCorner></gml:Envelope></ogc:BBOX>
    </ogc:And></ogc:Filter></csw:Constraint></csw:Query></csw:GetRecords>' \
  | grep -o '<csw:SearchResults[^>]*>\|<gmd:fileIdentifier>\s*<gco:CharacterString>[^<]*' | head -5

hr "6. GetRecordById - full ISO 19139"
curl -s -G "$CSW" --data-urlencode service=CSW --data-urlencode version=2.0.2 --data-urlencode request=GetRecordById \
  --data-urlencode "id=CartoSat-1_PAN_CartoDEM_30m" --data-urlencode outputSchema=http://www.isotc211.org/2005/gmd \
  --data-urlencode elementSetName=full | show 500

hr "7. Error handling - unsupported operation"
curl -s "$CSW?service=CSW&version=2.0.2&request=Nope" | grep -o 'exceptionCode="[A-Za-z]*"\|<ows:ExceptionText>[^<]*'
