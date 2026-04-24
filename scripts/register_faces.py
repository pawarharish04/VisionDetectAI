import boto3
import os
import re

rekognition = boto3.client('rekognition')

COLLECTION_ID = 'SmartCampusCollection'
IMAGE_DIR = 'sample_Images'

def sanitize_external_id(name):
    # ExternalImageId must match pattern [a-zA-Z0-9_.\-:]+
    sanitized = re.sub(r'[^a-zA-Z0-9_.\-:]', '_', name)
    return sanitized[:255] # Max length is 255

def main():
    if not os.path.exists(IMAGE_DIR):
        print(f"Directory {IMAGE_DIR} does not exist.")
        return

    for filename in os.listdir(IMAGE_DIR):
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            continue
            
        filepath = os.path.join(IMAGE_DIR, filename)
        base_name = os.path.splitext(filename)[0]
        external_id = f"Person_{sanitize_external_id(base_name)}"
        
        with open(filepath, 'rb') as f:
            image_bytes = f.read()
        
        try:
            response = rekognition.index_faces(
                CollectionId=COLLECTION_ID,
                Image={'Bytes': image_bytes},
                ExternalImageId=external_id,
                MaxFaces=1,
                QualityFilter='AUTO'
            )
            
            records = response.get('FaceRecords', [])
            if records:
                face_id = records[0]['Face']['FaceId']
                print(f"Indexed {filename} as {external_id} (FaceId: {face_id})")
            else:
                print(f"No faces detected in {filename}")
                
        except Exception as e:
            print(f"Failed to index {filename}: {e}")

if __name__ == '__main__':
    main()
