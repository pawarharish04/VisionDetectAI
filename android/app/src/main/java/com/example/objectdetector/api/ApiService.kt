package com.example.objectdetector.api

import com.example.objectdetector.models.DetectionResult
import com.example.objectdetector.models.PresignResponse
import okhttp3.RequestBody
import retrofit2.Response
import retrofit2.http.*

interface ApiService {
    @GET("presign")
    suspend fun getPresignUrl(
        @Query("filename") filename: String,
        @Query("contentType") contentType: String
    ): Response<PresignResponse>

    @PUT
    suspend fun uploadToS3(
        @Url url: String,
        @Header("Content-Type") contentType: String,
        @Body file: RequestBody
    ): Response<Unit>

    @GET("results/{imageKey}")
    suspend fun getResults(
        @Path("imageKey", encoded = true) imageKey: String
    ): Response<DetectionResult>
}
