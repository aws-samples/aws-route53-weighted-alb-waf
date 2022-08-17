#!/bin/bash

###################################################################
# Script Name	: waf-tests.sh
# Description	: Executes a series of tests against a WAF 
#                 protected ALB.
# Args          :
# Author       	: Damian McDonald
###################################################################

while getopts u: flag
do
    case "${flag}" in
        u) ALB_URL=${OPTARG};;
    esac
done

if [ -z "${ALB_URL}" ]
then
      echo "ALB URL is empty. Ensure you are executing the scipt with the -u flag"
      echo "Example: bash tests/scripts/waf-tests.sh -u http://alb-url.aws-region-1.elb.amazonaws.com"
      exit 0
fi

echo "###############################"
echo "Executing tests with ALB URL:"
echo "${ALB_URL}"
echo "###############################"

echo "###############################"
echo "Test with example params header"
echo "###############################"

RESPONSE=$(curl -s -X GET ${ALB_URL} \
    -H "Content-Type: application/json" \
    -H "ExampleParam01: Madrid" \
    -H "ExampleParam02: Istanbul" \
    -H "ExampleParam03: Sydney" \
    -H "ExampleParam04: Adelaide" \
    -H "ExampleParam05: Alicante" \
    -H "ExampleParam06: Seoul" \
    -H "ExampleParam07: Edinburgh")

echo ""
echo "Expecting a HTTP CODE 200 successful response. This is a request that WAF should NOT block."
echo "###############################"
echo "${RESPONSE}"
echo "###############################"
echo ""

echo "###############################"
echo "WAF Test 01: localhost header"
echo "###############################"

RESPONSE=$(curl -s -X GET ${ALB_URL} \
    -H "Content-Type: application/json" \
    -H "ExampleParam01: Madrid" \
    -H "ExampleParam02: Istanbul" \
    -H "ExampleParam03: Sydney" \
    -H "ExampleParam04: Adelaide" \
    -H "ExampleParam05: Alicante" \
    -H "ExampleParam06: Seoul" \
    -H "ExampleParam07: Edinburgh" \
    -H "Host: localhost")

echo ""
echo "Expecting a HTTP Code 403 Forbidden, proving that WAF has blocked the localhost header."
echo "###############################"
echo "${RESPONSE}"
echo "###############################"
echo ""

echo "###############################"
echo "WAF Test 02: Cross Site Scripting attack"
echo "###############################"

RESPONSE=$(curl -X POST  ${ALB_URL} -F "user='<script><alert>Hello></alert></script>'")

echo ""
echo "Expecting a HTTP Code 403 Forbidden, proving that WAF has blocked the Cross Site Scripting attack."
echo "###############################"
echo "${RESPONSE}"
echo "###############################"
echo ""

echo "###############################"
echo "WAF Test 03: Java deserialization Remote Command Execution(RCE) attempts"
echo "###############################"

RESPONSE=$(curl -X POST -d '{(java.lang.Runtime).getRuntime().exec("whoami")}' ${ALB_URL})

echo ""
echo "Expecting a HTTP Code 403 Forbidden, proving that WAF has blocked the Java deserialization Remote Command Execution(RCE) attempt."
echo "###############################"
echo "${RESPONSE}"
echo "###############################"
echo ""

echo "###############################"
echo "WAF Test 04: Log4j vulnerability"
echo "###############################"

RESPONSE=$(curl -X POST -d "{jndi:ldap://example.com/}" ${ALB_URL})

echo ""
echo "Expecting a HTTP Code 403 Forbidden, proving that WAF has blocked the Log4j vulnerability."
echo "###############################"
echo "${RESPONSE}"
echo "###############################"
echo ""