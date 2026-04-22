import os
import sys
import json
from unittest.mock import patch, MagicMock

# Set environment variables BEFORE importing handler
os.environ['TABLE_NAME'] = 'mock-table-dev'
os.environ['SNS_TOPIC_ARN'] = 'arn:aws:sns:us-east-1:123456789012:DetectionAlertsTopic'
os.environ['HIGH_CONFIDENCE_THRESHOLD'] = '90'
os.environ['TTL_DAYS'] = '30'
os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'

# We need to mock boto3 before importing handler because handler initializes clients at module level
with patch('boto3.client') as mock_client, patch('boto3.resource') as mock_resource:
    mock_rekognition = MagicMock()
    mock_sns = MagicMock()
    
    # Configure mock client side-effects
    def client_side_effect(service, *args, **kwargs):
        if service == 'rekognition':
            return mock_rekognition
        elif service == 'sns':
            return mock_sns
        return MagicMock()
    
    mock_client.side_effect = client_side_effect
    
    mock_dynamodb = MagicMock()
    mock_table = MagicMock()
    mock_dynamodb.Table.return_value = mock_table
    mock_resource.return_value = mock_dynamodb

    # Now import handler
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
    import handler

    # Setup Rekognition mock responses
    mock_rekognition.detect_labels.return_value = {
        'Labels': [
            {'Name': 'Dog', 'Confidence': 95.5},
            {'Name': 'Pet', 'Confidence': 92.1},
            {'Name': 'Fence', 'Confidence': 45.0} # Below threshold
        ]
    }
    mock_rekognition.detect_text.return_value = {
        'TextDetections': [{'DetectedText': 'Beware of Dog', 'Confidence': 99.0}]
    }
    mock_rekognition.detect_moderation_labels.return_value = {
        'ModerationLabels': []
    }

    test_event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-images-bucket"},
                    "object": {"key": "images/test%2Bimage.jpg"} # Encoded + sign (simulating test+image.jpg)
                }
            }
        ]
    }

    print("Running local test for handler.py ...\n")
    try:
        response = handler.lambda_handler(test_event, None)
        print("\n--- TEST SUCCESS ---")
        print("\n1. Lambda Response:")
        print(json.dumps(response, indent=2))
        
        # Verify DynamoDB Put
        print("\n2. DynamoDB put_item called with:")
        put_kwargs = mock_table.put_item.call_args[1]
        print(json.dumps(put_kwargs, default=str, indent=2)) # default=str handles Decimals
        
        # Verify SNS
        print("\n3. SNS publish called with:")
        if mock_sns.publish.called:
            sns_kwargs = mock_sns.publish.call_args[1]
            print(json.dumps(sns_kwargs, indent=2))
        else:
            print("SNS publish NOT called.")
            
    except Exception as e:
        print("\n--- TEST FAILED ---")
        import traceback
        traceback.print_exc()
