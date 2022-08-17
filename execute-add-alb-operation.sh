#!/bin/bash

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
SNS_ADD_ALB_TOPIC_ARN_EXPORT_NAME="SnsAddAlbTopicArnOutput"
ADD_ALB_STATE_MACHINE_EXPORT_NAME="AddAlbStateMachineArnOutput"

# print the assigned values
echo -e "${NC}CDK_STACK_NAME == ${GREEN}${CDK_STACK_NAME}${NC}"

# grab the arns from the Cloudformation outputs
echo -e "${NC}Grabbing the SnsAddAlbTopicArn from Cloudformation export: ${GREEN}${SNS_ADD_ALB_TOPIC_ARN_EXPORT_NAME}${NC}"
ADD_ALB_TOPIC_ARN=$(aws cloudformation describe-stacks --stack-name ${CDK_STACK_NAME} --query "Stacks[0].Outputs[?ExportName=='${SNS_ADD_ALB_TOPIC_ARN_EXPORT_NAME}'].OutputValue" --output text)
echo -e "${NC}Grabbing the AddAlbStateMachineArnOutput from Cloudformation export: ${GREEN}${ADD_ALB_STATE_MACHINE_EXPORT_NAME}${NC}"
ADD_ALB_STATE_MACHINE_ARN=$(aws cloudformation describe-stacks --stack-name ${CDK_STACK_NAME} --query "Stacks[0].Outputs[?ExportName=='${ADD_ALB_STATE_MACHINE_EXPORT_NAME}'].OutputValue" --output text)

echo -e "${NC}ADD_ALB_TOPIC_ARN == ${GREEN}${ADD_ALB_TOPIC_ARN}${NC}"

# set the ssm params to allow for alb addition
SUSPEND_ADD_ALB_PARAM_NAME="/weighted-alb-with-waf/add-alb-suspend"
aws ssm put-parameter --name ${SUSPEND_ADD_ALB_PARAM_NAME} --value "false" --overwrite

# execute an add alb operation
echo -e "${NC}Executing an Add Alb operation"
aws sns publish --topic-arn ${ADD_ALB_TOPIC_ARN} --message "Add alb operation invoke"

echo -e "${NC}The state machine shown below is now executing the ALB add operation."
echo -e "    ${GREEN}${ADD_ALB_STATE_MACHINE_ARN}${NC}"