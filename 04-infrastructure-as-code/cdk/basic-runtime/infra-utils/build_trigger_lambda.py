import boto3
import cfnresponse
import json
import logging
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    logger.info('Received event: %s', json.dumps(event))
    
    try:
        if event['RequestType'] == 'Delete':
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return
            
        project_name = event['ResourceProperties']['ProjectName']
        
        codebuild = boto3.client('codebuild')
        
        # Start build
        response = codebuild.start_build(projectName=project_name)
        build_id = response['build']['id']
        logger.info(f"Started build: {build_id}")
        
        # Wait for completion
        max_wait_time = context.get_remaining_time_in_millis() / 1000 - 30
        start_time = time.time()
        
        while True:
            if time.time() - start_time > max_wait_time:
                cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': 'Build timeout'})
                return
                
            build_response = codebuild.batch_get_builds(ids=[build_id])
            build_status = build_response['builds'][0]['buildStatus']
            
            if build_status == 'SUCCEEDED':
                logger.info(f"Build {build_id} succeeded")
                cfnresponse.send(event, context, cfnresponse.SUCCESS, {'BuildId': build_id})
                return
            elif build_status in ['FAILED', 'FAULT', 'STOPPED', 'TIMED_OUT']:
                logger.error(f"Build {build_id} failed with status: {build_status}")
                cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': f'Build failed: {build_status}'})
                return
                
            logger.info(f"Build {build_id} status: {build_status}")
            time.sleep(30)
            
    except Exception as e:
        logger.error('Error: %s', str(e))
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
