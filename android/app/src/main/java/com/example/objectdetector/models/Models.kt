package com.example.objectdetector.models

data class PresignResponse(
    val uploadUrl: String,
    val imageKey: String,
    val expiresIn: Int
)

data class DetectionResult(
    val status: String,
    val annotatedUrl: String? = null,
    val result: String? = null, // Stringified JSON from DynamoDB
    val errorMessage: String? = null
)

data class RekognitionResult(
    val labels: List<Label>? = null
)

data class Label(
    val Name: String,
    val Confidence: Double
)
