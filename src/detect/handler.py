import json
import os
import time
import urllib.parse
import traceback
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import boto3

# Initialize AWS clients outside the handler for connection reuse
rekognition = boto3.client('rekognition')
dynamodb = boto3.resource('dynamodb')
sns = boto3.client('sns')

# Load environment variables
TABLE_NAME = os.environ.get('TABLE_NAME')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
HIGH_CONFIDENCE_THRESHOLD = float(os.environ.get('HIGH_CONFIDENCE_THRESHOLD', '90'))
TTL_DAYS = int(os.environ.get('TTL_DAYS', '30'))

def log_event(level, message, **kwargs):
    """Structured CloudWatch logging using print() as JSON"""
    log_entry = {
        "level": level,
        "message": message,
        **kwargs
    }
    print(json.dumps(log_entry))

def parse_float_to_decimal(obj):
    """Recursively converts floats to Decimals for DynamoDB compatibility"""
    if isinstance(obj, list):
        return [parse_float_to_decimal(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: parse_float_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, float):
        # Convert float to Decimal (using string intermediate to avoid precision issues)
        return Decimal(str(obj))
    return obj

def detect_labels(image_info):
    log_event("INFO", "Calling rekognition.detect_labels")
    return rekognition.detect_labels(
        Image=image_info,
        MaxLabels=20,
        MinConfidence=50
    )

def detect_text(image_info):
    log_event("INFO", "Calling rekognition.detect_text")
    return rekognition.detect_text(
        Image=image_info
    )

def detect_moderation_labels(image_info):
    log_event("INFO", "Calling rekognition.detect_moderation_labels")
    return rekognition.detect_moderation_labels(
        Image=image_info,
        MinConfidence=50
    )

def detect_protective_equipment(image_info):
    log_event("INFO", "Calling rekognition.detect_protective_equipment")
    return rekognition.detect_protective_equipment(
        Image=image_info
    )

def lambda_handler(event, context):
    start_time = time.time()
    
    try:
        log_event("INFO", "Received event", event=event)
        
        # 1. Parse the S3 event to get bucket + key
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        log_event("INFO", "Processing S3 object", bucket=bucket, key=key)
        
        image_info = {'S3Object': {'Bucket': bucket, 'Name': key}}
        
        # 2. Run Rekognition calls IN PARALLEL using ThreadPoolExecutor
        results = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_labels = executor.submit(detect_labels, image_info)
            future_text = executor.submit(detect_text, image_info)
            future_mod = executor.submit(detect_moderation_labels, image_info)
            future_ppe = executor.submit(detect_protective_equipment, image_info)
            
            results['labels'] = future_labels.result().get('Labels', [])
            results['text'] = future_text.result().get('TextDetections', [])
            results['moderation'] = future_mod.result().get('ModerationLabels', [])
            results['ppe'] = future_ppe.result()
            
        log_event("INFO", "Rekognition parallel calls completed")
        
        # Transform results to be DynamoDB compatible (floats to Decimals)
        labels = parse_float_to_decimal(results['labels'])
        text_detections = parse_float_to_decimal(results['text'])
        moderation_labels = parse_float_to_decimal(results['moderation'])
        ppe_raw = parse_float_to_decimal(results['ppe'])
        
        persons = ppe_raw.get("Persons", [])
        ppe_summary = ppe_raw.get("Summary", {})
        
        if not persons:
            ppe_compliance_status = "N/A"
            persons_without_ppe = 0
        else:
            persons_without_ppe = len(ppe_summary.get("PersonsWithoutRequiredEquipment", []))
            ppe_compliance_status = "COMPLIANT" if persons_without_ppe == 0 else "NON_COMPLIANT"
        
        label_count = len(labels)
        processing_time_ms = int((time.time() - start_time) * 1000)
        
        # 3. Save the combined result to DynamoDB
        table = dynamodb.Table(TABLE_NAME)
        timestamp_ms = int(time.time() * 1000)
        now_sec = int(time.time())
        ttl_val = now_sec + (TTL_DAYS * 86400)
        
        item = {
            'imageKey': key,
            'timestamp': timestamp_ms,
            'status': 'COMPLETE',
            'statusPk': 'COMPLETE',
            'labels': labels,
            'text_detections': text_detections,
            'moderation_labels': moderation_labels,
            'ppe': ppe_raw,
            'ppe_raw': ppe_raw,
            'ppe_compliance_status': ppe_compliance_status,
            'persons_without_ppe': persons_without_ppe,
            'ttl': ttl_val,
            'label_count': label_count,
            'processing_time_ms': processing_time_ms
        }
        
        table.put_item(Item=item)
        log_event("INFO", "Saved results to DynamoDB", imageKey=key, processingTimeMs=processing_time_ms)
        
        # 4. Extract labels >= HIGH_CONFIDENCE_THRESHOLD and publish to SNS
        high_conf_labels = [l for l in results['labels'] if l.get('Confidence', 0) >= HIGH_CONFIDENCE_THRESHOLD]
        if high_conf_labels and SNS_TOPIC_ARN:
            top_labels_info = ", ".join([f"{l['Name']} ({l['Confidence']:.2f}%)" for l in high_conf_labels[:5]])
            sns_msg = f"High confidence objects detected in image: {key}\nTop Labels: {top_labels_info}"
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="High Confidence Object Detected",
                Message=sns_msg
            )
            log_event("INFO", "Published high confidence alert to SNS", topicArn=SNS_TOPIC_ARN, count=len(high_conf_labels))

        # 5. Return the imageKey and label count
        response = {
            "imageKey": key,
            "label_count": label_count
        }
        log_event("INFO", "Execution successful", result=response)
        return response
        
    except Exception as e:
        log_event("ERROR", "Error processing S3 object", error=str(e), error_type=type(e).__name__, traceback=traceback.format_exc())
        raise
