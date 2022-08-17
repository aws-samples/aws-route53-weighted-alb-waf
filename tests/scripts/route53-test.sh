#!/bin/bash
echo "Testing Route 53 routing distriibution to ALB weighted resource set"
for i in {1..10000}
do
    domain=$(dig alb.weightedalbwithwaf.internal A @169.254.169.253 +short)
    echo -e  "$domain" >> RecursiveResolver_results.txt
done

cat RecursiveResolver_results.txt | tr '[:space:]' '[\n*]' | grep -v "^\s*$" | sort | uniq -c | sort -bnr