#!/bin/bash

###################################################################
# Script Name     : execute-remove-alb-operation.sh
# Description     : Executes an ALB scale-in operation in which
#                   an ALB is destroyed, disassociated from WAF and
#                   removed from the Route53 private hosted zone.
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

# Reset
NC='\033[0m'       # Text Reset

# Regular Colors
BLACK='\033[0;30m'        # Black
RED='\033[0;31m'          # Red
GREEN='\033[0;32m'        # Green
YELLOW='\033[0;33m'       # Yellow
BLUE='\033[0;34m'         # Blue
PURPLE='\033[0;35m'       # Purple
CYAN='\033[0;36m'         # Cyan
WHITE='\033[0;37m'        # White

# Get project values
CDK_STACK_NAME="WeightedAlbWithWaf"
SNS_REMOVE_ALB_TOPIC_ARN_EXPORT_NAME="SnsRemoveAlbTopicArnOutput"
REMOVE_ALB_STATE_MACHINE_EXPORT_NAME="RemoveAlbStateMachineArnOutput"

# print the assigned values
echo -e "${NC}CDK_STACK_NAME == ${GREEN}${CDK_STACK_NAME}${NC}"

# grab the arns from the Cloudformation outputs
echo -e "${NC}Grabbing the SnsRemoveAlbTopicArnOutput from Cloudformation Ouput key: ${GREEN}${SNS_REMOVE_ALB_TOPIC_ARN_EXPORT_NAME}${NC}"
REMOVE_ALB_TOPIC_ARN=$(aws cloudformation describe-stacks --stack-name ${CDK_STACK_NAME} --query "Stacks[0].Outputs[?ExportName=='${SNS_REMOVE_ALB_TOPIC_ARN_EXPORT_NAME}'].OutputValue" --output text)
echo -e "${NC}Grabbing the RemoveAlbStateMachineArnOutput from Cloudformation Ouput key: ${GREEN}${REMOVE_ALB_STATE_MACHINE_EXPORT_NAME}${NC}"
REMOVE_ALB_STATE_MACHINE_ARN=$(aws cloudformation describe-stacks --stack-name ${CDK_STACK_NAME} --query "Stacks[0].Outputs[?ExportName=='${REMOVE_ALB_STATE_MACHINE_EXPORT_NAME}'].OutputValue" --output text)

echo -e "${NC}REMOVE_ALB_TOPIC_ARN == ${GREEN}${REMOVE_ALB_TOPIC_ARN}${NC}"

# set the ssm param to allow for alb removal
SUSPEND_REMOVE_ALB_PARAM_NAME="/weighted-alb-with-waf/remove-alb-suspend"
aws ssm put-parameter --name ${SUSPEND_REMOVE_ALB_PARAM_NAME} --value "false" --overwrite

# execute an add alb operation
echo -e "${NC}Executing a Remove Alb operation"
aws sns publish --topic-arn ${REMOVE_ALB_TOPIC_ARN} --message "Remove alb operation invoke"

echo -e "${NC}The state machine shown below is now executing the ALB remove operation."
echo -e "    ${GREEN}${REMOVE_ALB_STATE_MACHINE_ARN}${NC}"