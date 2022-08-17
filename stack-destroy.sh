#!/bin/bash

AWS_REGION=eu-west-1
STACK_NAME="WeightedAlbWithWaf"
FUNCTION_EXPORT_NAME="DynamicResourceCleanerLambdaNameOutput"
FUNCTION_NAME=$(aws cloudformation --region ${AWS_REGION} describe-stacks --stack-name ${STACK_NAME} --query "Stacks[0].Outputs[?ExportName=='${FUNCTION_EXPORT_NAME}'].OutputValue" --output text)
SUSPEND_ADD_ALB_PARAM_NAME="/weighted-alb-with-waf/add-alb-suspend"
SUSPEND_REMOVE_ALB_PARAM_NAME="/weighted-alb-with-waf/remove-alb-suspend"

echo "Pause any remove alb operations to allow for dynamic resource deletion"
aws ssm put-parameter --name ${SUSPEND_ADD_ALB_PARAM_NAME} --value "true" --overwrite
aws ssm put-parameter --name ${SUSPEND_REMOVE_ALB_PARAM_NAME} --value "false" --overwrite

echo "Invoking dynamic resource cleanup ..."
aws lambda invoke \
    --function-name ${FUNCTION_NAME} \
    response.json

echo "Destroying the IaC stack ..."
cdk destroy --force