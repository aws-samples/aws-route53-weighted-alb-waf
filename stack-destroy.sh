#!/bin/bash

###################################################################
# Script Name     : deploy.sh
# Description     : Destroys CDK resources for the project
# Args            :
# Author          : Damian McDonald
###################################################################

### <START> check if AWS credential variables are correctly set
if [ -z "${AWS_ACCESS_KEY_ID}" ]
then
      echo "AWS credential variable AWS_ACCESS_KEY_ID is empty."
      echo "Please see the guide below for instructions on how to configure your AWS CLI environment."
      echo "https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html"
fi

if [ -z "${AWS_SECRET_ACCESS_KEY}" ]
then
      echo "AWS credential variable AWS_SECRET_ACCESS_KEY is empty."
      echo "Please see the guide below for instructions on how to configure your AWS CLI environment."
      echo "https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html"
fi

if [ -z "${AWS_DEFAULT_REGION}" ]
then
      echo "AWS credential variable AWS_DEFAULT_REGION is empty."
      echo "Please see the guide below for instructions on how to configure your AWS CLI environment."
      echo "https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html"
fi
### </END> check if AWS credential variables are correctly set

STACK_NAME="WeightedAlbWithWaf"
FUNCTION_EXPORT_NAME="DynamicResourceCleanerLambdaNameOutput"
FUNCTION_NAME=$(aws cloudformation --region ${AWS_DEFAULT_REGION} describe-stacks --stack-name ${STACK_NAME} --query "Stacks[0].Outputs[?ExportName=='${FUNCTION_EXPORT_NAME}'].OutputValue" --output text)
SUSPEND_ADD_ALB_PARAM_NAME="/weighted-alb-with-waf/add-alb-suspend"
SUSPEND_REMOVE_ALB_PARAM_NAME="/weighted-alb-with-waf/remove-alb-suspend"

echo "############################"
echo "<START> EXECUTING DYNAMIC RESOURCE DELETION"
echo ""
echo "Pause any remove alb operations to allow for dynamic resource deletion"
aws ssm put-parameter --name ${SUSPEND_ADD_ALB_PARAM_NAME} --value "true" --overwrite
aws ssm put-parameter --name ${SUSPEND_REMOVE_ALB_PARAM_NAME} --value "false" --overwrite

echo "Invoking dynamic resource cleanup ..."
aws lambda invoke \
    --function-name ${FUNCTION_NAME} \
    response.json
echo ""
echo "</END> EXECUTING DYNAMIC RESOURCE DELETION"
echo ""

echo "############################"
echo "<START> EXECUTING CDK DESTROY"
echo ""
cdk destroy
echo ""
echo "</END> EXECUTING CDK DESTROY"
echo ""