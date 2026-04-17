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
    val errorMessage: String? = null,
    val compliance_status: String? = null,
    val persons_detected: Int? = null,
    val persons_without_ppe: Int? = null,
    val ppe_reasoning: List<String>? = null
)

data class RekognitionResult(
    val labels: List<Label>? = null,
    val ppe: PPEResult? = null
)

data class PPEResult(
    val Summary: PPESummary? = null
)

data class PPESummary(
    val PersonsWithRequiredEquipment: List<Int>? = null,
    val PersonsWithoutRequiredEquipment: List<Int>? = null,
    val PersonsIndeterminate: List<Int>? = null
)

data class Label(
    val Name: String,
    val Confidence: Double
)
