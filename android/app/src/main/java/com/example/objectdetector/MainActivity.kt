package com.example.objectdetector

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.view.View
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.example.objectdetector.api.ApiClient
import com.example.objectdetector.ui.ResultActivity
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.toRequestBody

class MainActivity : AppCompatActivity() {
    private lateinit var selectedImageUri: Uri
    private val api = ApiClient.instance
    
    private lateinit var ivPreview: ImageView
    private lateinit var btnUpload: Button
    private lateinit var progressBar: ProgressBar
    private lateinit var tvStatus: TextView

    private val pickImage = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) {
            val data: Intent? = result.data
            selectedImageUri = data?.data!!
            ivPreview.setImageURI(selectedImageUri)
            btnUpload.isEnabled = true
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        ivPreview = findViewById(R.id.ivPreview)
        btnUpload = findViewById(R.id.btnUpload)
        progressBar = findViewById(R.id.progressBar)
        tvStatus = findViewById(R.id.tvStatus)

        findViewById<Button>(R.id.btnSelect).setOnClickListener {
            val intent = Intent(Intent.ACTION_PICK, MediaStore.Images.Media.EXTERNAL_CONTENT_URI)
            pickImage.launch(intent)
        }

        btnUpload.setOnClickListener { startUpload() }
    }

    private fun startUpload() = lifecycleScope.launch {
        progressBar.visibility = View.VISIBLE
        tvStatus.visibility = View.VISIBLE
        btnUpload.isEnabled = false
        
        try {
            tvStatus.text = "Requesting secure link..."
            val presignRes = api.getPresignUrl("android_upload.jpg", "image/jpeg")
            
            if (presignRes.isSuccessful) {
                val data = presignRes.body()!!
                
                tvStatus.text = "Uploading to AWS S3..."
                val inputStream = contentResolver.openInputStream(selectedImageUri)
                val bytes = inputStream?.readBytes() ?: throw Exception("Failed to read image")
                val requestBody = bytes.toRequestBody("image/jpeg".toMediaTypeOrNull())
                
                val uploadRes = api.uploadToS3(data.uploadUrl, "image/jpeg", requestBody)
                
                if (uploadRes.isSuccessful) {
                    val intent = Intent(this@MainActivity, ResultActivity::class.java)
                    intent.putExtra("IMAGE_KEY", data.imageKey)
                    startActivity(intent)
                } else {
                    throw Exception("S3 Upload Failed")
                }
            } else {
                throw Exception("Presign Failed")
            }
        } catch (e: Exception) {
            Toast.makeText(this@MainActivity, "Error: ${e.message}", Toast.LENGTH_LONG).show()
            btnUpload.isEnabled = true
        } finally {
            progressBar.visibility = View.GONE
            tvStatus.visibility = View.GONE
        }
    }
}
