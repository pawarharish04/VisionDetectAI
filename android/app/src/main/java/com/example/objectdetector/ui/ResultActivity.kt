package com.example.objectdetector.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.example.objectdetector.R
import com.example.objectdetector.api.ApiClient
import com.example.objectdetector.models.Label
import com.example.objectdetector.models.RekognitionResult
import com.google.gson.Gson
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class ResultActivity : AppCompatActivity() {
    private val api = ApiClient.instance
    private lateinit var imageKey: String
    private lateinit var labelsAdapter: LabelsAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_result)

        imageKey = intent.getStringExtra("IMAGE_KEY") ?: ""
        
        val rvLabels = findViewById<RecyclerView>(R.id.rvLabels)
        rvLabels.layoutManager = LinearLayoutManager(this)
        labelsAdapter = LabelsAdapter(emptyList())
        rvLabels.adapter = labelsAdapter

        findViewById<View>(R.id.btnBack).setOnClickListener { finish() }

        pollForResults()
    }

    private fun pollForResults() = lifecycleScope.launch {
        while (true) {
            try {
                val res = api.getResults(imageKey)
                if (res.isSuccessful) {
                    val data = res.body()!!
                    if (data.status == "complete" && data.annotatedUrl != null) {
                        displayData(data)
                        break
                    }
                }
            } catch (e: Exception) {
                // Ignore transient errors while polling
            }
            delay(2000)
        }
    }

    private fun displayData(data: com.example.objectdetector.models.DetectionResult) {
        val ivAnnotated = findViewById<ImageView>(R.id.ivAnnotated)
        val tvWarning = findViewById<TextView>(R.id.tvWarning)
        
        Glide.with(this).load(data.annotatedUrl).into(ivAnnotated)

        // Show PPE Compliance Warning
        if (data.compliance_status == "FAIL") {
            tvWarning.visibility = View.VISIBLE
            val reasoningText = data.ppe_reasoning?.joinToString("\n") ?: ""
            tvWarning.text = "PPE WARNING: ${data.persons_without_ppe} / ${data.persons_detected} missing required safety gear!\n$reasoningText"
            tvWarning.setBackgroundColor(android.graphics.Color.parseColor("#EF4444")) // Red
        } else if (data.compliance_status == "PASS") {
            tvWarning.visibility = View.VISIBLE
            tvWarning.text = "All ${data.persons_detected} workers are compliant. Great job!"
            tvWarning.setBackgroundColor(android.graphics.Color.parseColor("#10B981")) // Green
        } else {
            tvWarning.visibility = View.GONE
        }

        val result = Gson().fromJson(data.result, RekognitionResult::class.java)
        result.labels?.let {
            labelsAdapter.updateData(it)
        }
    }
}

class LabelsAdapter(private var labels: List<Label>) : RecyclerView.Adapter<LabelsAdapter.VH>() {
    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val name: TextView = v.findViewById(R.id.tvLabelName)
        val conf: TextView = v.findViewById(R.id.tvConfidence)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context).inflate(R.layout.item_label, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val l = labels[position]
        holder.name.text = l.Name
        holder.conf.text = "${l.Confidence.toInt()}%"
    }

    override fun getItemCount() = labels.size

    fun updateData(newLabels: List<Label>) {
        labels = newLabels
        notifyDataSetChanged()
    }
}
