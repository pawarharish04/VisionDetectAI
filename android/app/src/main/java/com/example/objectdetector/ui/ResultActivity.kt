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
                        displayData(data.annotatedUrl, data.result)
                        break
                    }
                }
            } catch (e: Exception) {
                // Ignore transient errors while polling
            }
            delay(2000)
        }
    }

    private fun displayData(url: String, resultJson: String?) {
        val ivAnnotated = findViewById<ImageView>(R.id.ivAnnotated)
        Glide.with(this).load(url).into(ivAnnotated)

        val result = Gson().fromJson(resultJson, RekognitionResult::class.java)
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
