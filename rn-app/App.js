import { StatusBar } from 'expo-status-bar';
import React, { useState } from 'react';
import { 
  StyleSheet, Text, View, Button, Image, ActivityIndicator, 
  ScrollView, TouchableOpacity, Modal 
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import ImageViewer from 'react-native-image-zoom-viewer';

// Update this to your deployed API Gateway endpoint
// ── CONFIGURATION ──────────────────────────────────────────────────────────
// Update this URL with your actual API Gateway base URL from SAM outputs
const DEFAULT_API_BASE_URL = 'https://yo5zq9f8qj.execute-api.us-east-1.amazonaws.com/dev';

export default function App() {
  const [apiBaseUrl, setApiBaseUrl] = useState(DEFAULT_API_BASE_URL);
  const [isConfiguring, setIsConfiguring] = useState(false);
  const [activeTab, setActiveTab] = useState('UPLOAD'); // 'UPLOAD' or 'HISTORY'
  const [imageUri, setImageUri] = useState(null);
  const [status, setStatus] = useState('IDLE'); // IDLE, UPLOADING, DETECTING, SUCCESS, ERROR
  const [resultData, setResultData] = useState(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [isZoomVisible, setIsZoomVisible] = useState(false);
  const [historyData, setHistoryData] = useState([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  const fetchHistory = async () => {
    setIsLoadingHistory(true);
    try {
      const res = await fetch(`${apiBaseUrl}/history`);
      if (!res.ok) throw new Error('Failed to fetch history');
      const data = await res.json();
      setHistoryData(data.history || []);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const pickImage = async () => {
    let result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: true,
      quality: 0.8,
    });

    if (!result.canceled) {
      setImageUri(result.assets[0].uri);
      setStatus('IDLE');
      setResultData(null);
      setErrorMessage('');
      setIsZoomVisible(false);
    }
  };

  const uploadAndDetect = async () => {
    if (!imageUri) return;
    setStatus('UPLOADING');
    setErrorMessage('');

    try {
      // 1. Get Presigned URL
      const filename = `upload-${Date.now()}.jpg`;
      const presignRes = await fetch(`${apiBaseUrl}/presign?filename=${filename}&contentType=image/jpeg`);
      if (!presignRes.ok) throw new Error('Failed to get presigned URL');
      const presignData = await presignRes.json();

      // 2. Convert local image to blob
      const fileRes = await fetch(imageUri);
      const blob = await fileRes.blob();

      // 3. Upload to S3
      const uploadRes = await fetch(presignData.uploadUrl, {
        method: 'PUT',
        body: blob,
        headers: { 'Content-Type': 'image/jpeg' },
      });
      if (!uploadRes.ok) throw new Error('Failed to upload image to S3');

      setStatus('DETECTING');
      pollForResult(presignData.imageKey);

    } catch (error) {
      setStatus('ERROR');
      setErrorMessage(error.message);
    }
  };

  const pollForResult = async (imageKey) => {
    const encodedKey = encodeURIComponent(imageKey);
    let attempts = 0;
    const maxAttempts = 30; // 1 minute max wait time (2s intervals)

    const poll = setInterval(async () => {
      try {
        attempts++;
        if (attempts >= maxAttempts) {
          clearInterval(poll);
          setStatus('ERROR');
          setErrorMessage('Detection timed out.');
          return;
        }

        const res = await fetch(`${apiBaseUrl}/results/${encodedKey}`);
        if (res.status === 404) return; // Keep waiting

        if (!res.ok) {
          clearInterval(poll);
          throw new Error('API Error');
        }

        const data = await res.json();
        
        if (data.status === 'failed') {
          clearInterval(poll);
          setStatus('ERROR');
          setErrorMessage(data.errorMessage || 'Lambda detection failed');
        } else if (data.status === 'complete' && data.annotatedUrl) {
          clearInterval(poll);
          setStatus('SUCCESS');
          setResultData(data);
        }
      } catch (error) {
        clearInterval(poll);
        setStatus('ERROR');
        setErrorMessage(error.message);
      }
    }, 2000);
  };

  const parseResultJson = () => {
    if (!resultData || !resultData.result) return {};
    return typeof resultData.result === 'string' ? JSON.parse(resultData.result) : resultData.result;
  };

  const renderPPEWarning = () => {
    if (!resultData?.compliance_status) return null;

    const { compliance_status, persons_without_ppe, persons_detected, ppe_reasoning } = resultData;
    if (compliance_status === 'FAIL') {
      return (
        <View style={[styles.ppeBox, styles.ppeFail]}>
          <Text style={styles.ppeTitle}>⚠️ PPE WARNING: {persons_without_ppe} / {persons_detected} missing gear!</Text>
          {(ppe_reasoning || []).map((reason, idx) => (
            <Text key={idx} style={styles.ppeReason}>• {reason}</Text>
          ))}
        </View>
      );
    } else if (compliance_status === 'PASS') {
      return (
        <View style={[styles.ppeBox, styles.ppePass]}>
          <Text style={styles.ppeTitle}>✅ ALL {persons_detected} workers compliant!</Text>
        </View>
      );
    }
    return null;
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>VisionGuard</Text>
        <TouchableOpacity onPress={() => setIsConfiguring(!isConfiguring)}>
          <Text style={styles.settingsBtn}>{isConfiguring ? '✕ Close' : '⚙️ Config'}</Text>
        </TouchableOpacity>
      </View>

      {isConfiguring && (
        <View style={styles.configBox}>
          <Text style={styles.configLabel}>API Base URL (from SAM outputs):</Text>
          <TextInput
            style={styles.configInput}
            value={apiBaseUrl}
            onChangeText={setApiBaseUrl}
            placeholder="https://...execute-api.us-east-1.amazonaws.com/dev"
            placeholderTextColor="#666"
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>
      )}

      <View style={styles.tabs}>
        <TouchableOpacity 
          style={[styles.tab, activeTab === 'UPLOAD' && styles.activeTab]} 
          onPress={() => setActiveTab('UPLOAD')}
        >
          <Text style={[styles.tabText, activeTab === 'UPLOAD' && styles.activeTabText]}>Upload</Text>
        </TouchableOpacity>
        <TouchableOpacity 
          style={[styles.tab, activeTab === 'HISTORY' && styles.activeTab]} 
          onPress={() => { setActiveTab('HISTORY'); fetchHistory(); }}
        >
          <Text style={[styles.tabText, activeTab === 'HISTORY' && styles.activeTabText]}>History</Text>
        </TouchableOpacity>
      </View>

      {activeTab === 'UPLOAD' ? (
      <View>
        {/* Image Zoom Modal */}
      <Modal visible={isZoomVisible} transparent={true}>
        <ImageViewer 
          imageUrls={[{ url: resultData ? `${resultData.annotatedUrl}${resultData.annotatedUrl.includes('?') ? '&' : '?'}t=${Date.now()}` : imageUri }]}
          enableSwipeDown={true}
          onCancel={() => setIsZoomVisible(false)}
          onClick={() => setIsZoomVisible(false)}
          renderIndicator={() => null}
        />
      </Modal>

      {/* Image Preview / Result Image */}
      <View style={styles.imageContainer}>
        {status === 'SUCCESS' && resultData ? (
          <TouchableOpacity onPress={() => setIsZoomVisible(true)} style={styles.image}>
            <Image source={{ uri: `${resultData.annotatedUrl}${resultData.annotatedUrl.includes('?') ? '&' : '?'}t=${Date.now()}` }} style={styles.image} resizeMode="contain" />
          </TouchableOpacity>
        ) : imageUri ? (
          <TouchableOpacity onPress={() => setIsZoomVisible(true)} style={styles.image}>
            <Image source={{ uri: imageUri }} style={styles.image} resizeMode="contain" />
          </TouchableOpacity>
        ) : (
          <View style={styles.placeholder}>
            <Text style={styles.placeholderText}>Select an image to analyze</Text>
          </View>
        )}
      </View>

      {/* Controls */}
      <View style={styles.controls}>
        <Button title="Pick Image" onPress={pickImage} disabled={status === 'UPLOADING' || status === 'DETECTING'} />
        {imageUri && status !== 'SUCCESS' && (
          <View style={{ marginTop: 10 }}>
            <Button title="Run Detection" color="#10B981" onPress={uploadAndDetect} disabled={status === 'UPLOADING' || status === 'DETECTING'} />
          </View>
        )}
        <Button title="Reset" color="#64748B" onPress={() => {setImageUri(null); setStatus('IDLE'); setResultData(null); setErrorMessage('');}} />
      </View>

      {/* Status States */}
      {status === 'UPLOADING' && <Text style={styles.statusText}>Uploading image...</Text>}
      {status === 'DETECTING' && (
        <View style={styles.loadingRow}>
          <ActivityIndicator size="small" color="#3B82F6" />
          <Text style={styles.statusText}> Analyzing image for PPE...</Text>
        </View>
      )}
      {status === 'ERROR' && <Text style={styles.errorText}>{errorMessage}</Text>}

      {/* Results */}
      {status === 'SUCCESS' && (
        <View style={styles.resultsContainer}>
          {renderPPEWarning()}
          
          {resultData?.ai_safety_report && (
            <View style={styles.aiBox}>
              <Text style={styles.aiTitle}>🤖 Generative AI Safety Report</Text>
              <Text style={styles.aiBody}>{resultData.ai_safety_report}</Text>
            </View>
          )}

          <Text style={styles.resultsHeader}>Detected Objects</Text>
          {parseResultJson().labels?.map((label, i) => (
            <View key={i} style={styles.labelRow}>
              <Text style={styles.labelName}>{label.Name}</Text>
              <Text style={styles.labelConf}>{label.Confidence.toFixed(1)}%</Text>
            </View>
          ))}
        </View>
      )}
      </View>
      ) : (
        <View style={styles.historyContainer}>
          <Text style={styles.resultsHeader}>Compliance History</Text>
          {isLoadingHistory ? (
             <ActivityIndicator size="large" color="#3B82F6" style={{ marginTop: 20 }} />
          ) : (
             historyData.map((item, i) => (
               <View key={i} style={styles.historyCard}>
                 <Text style={styles.hDate}>{new Date(item.timestamp * 1000).toLocaleString()}</Text>
                 <Text style={styles.hStatus}>
                    Status: <Text style={{ fontWeight: 'bold', color: item.compliance_status === 'PASS' ? '#4ADE80' : item.compliance_status === 'FAIL' ? '#F87171' : '#94A3B8' }}>{item.compliance_status}</Text>
                 </Text>
                 <Text style={styles.hSummary}>{item.ai_safety_report || "No AI Report Available"}</Text>
               </View>
             ))
           )}
        </View>
      )}

      <StatusBar style="auto" />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0F172A' },
  content: { padding: 20, paddingTop: 60, paddingBottom: 40 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 },
  title: { fontSize: 24, fontWeight: 'bold', color: '#F8FAFC' },
  settingsBtn: { color: '#3B82F6', fontWeight: 'bold' },

  configBox: { backgroundColor: '#1E293B', padding: 15, borderRadius: 8, marginBottom: 20, borderLeftWidth: 4, borderLeftColor: '#3B82F6' },
  configLabel: { color: '#94A3B8', fontSize: 12, marginBottom: 8, fontWeight: 'bold' },
  configInput: { color: '#F8FAFC', fontSize: 14, backgroundColor: '#0F172A', padding: 10, borderRadius: 4, borderWidth: 1, borderColor: '#334155' },
  
  tabs: { flexDirection: 'row', marginBottom: 20, backgroundColor: '#1E293B', borderRadius: 8, padding: 4 },
  tab: { flex: 1, paddingVertical: 10, borderRadius: 6, alignItems: 'center' },
  activeTab: { backgroundColor: '#3B82F6' },
  tabText: { color: '#94A3B8', fontWeight: 'bold' },
  activeTabText: { color: '#FFFFFF' },

  historyContainer: { marginTop: 10 },
  historyCard: { backgroundColor: '#1E293B', padding: 15, borderRadius: 8, marginBottom: 12 },
  hDate: { color: '#94A3B8', fontSize: 12, marginBottom: 4 },
  hStatus: { color: '#E2E8F0', fontSize: 14, marginBottom: 8 },
  hSummary: { color: '#F8FAFC', fontSize: 13, lineHeight: 20 },
  imageContainer: { width: '100%', height: 350, backgroundColor: '#1E293B', borderRadius: 12, overflow: 'hidden', marginBottom: 20 },
  image: { width: '100%', height: '100%' },
  placeholder: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  placeholderText: { color: '#64748B' },
  controls: { marginBottom: 20 },
  statusText: { color: '#94A3B8', textAlign: 'center', fontSize: 16, marginVertical: 10 },
  loadingRow: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', marginVertical: 10 },
  errorText: { color: '#EF4444', textAlign: 'center', fontSize: 16, marginVertical: 10 },
  resultsContainer: { marginTop: 10 },
  resultsHeader: { fontSize: 18, fontWeight: 'bold', color: '#F8FAFC', marginBottom: 10 },
  labelRow: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: '#1E293B', padding: 12, borderRadius: 8, marginBottom: 8 },
  labelName: { color: '#E2E8F0', fontSize: 16 },
  labelConf: { color: '#94A3B8', fontSize: 16, fontWeight: 'bold' },
  ppeBox: { padding: 15, borderRadius: 8, marginBottom: 15, borderWidth: 1 },
  ppeFail: { backgroundColor: '#FEF2F2', borderColor: '#F87171' },
  ppePass: { backgroundColor: '#F0FDF4', borderColor: '#4ADE80' },
  ppeTitle: { fontSize: 16, fontWeight: 'bold', marginBottom: 5 },
  ppeReason: { fontSize: 14, marginLeft: 10, marginTop: 4 },
  aiBox: { backgroundColor: '#1E293B', padding: 15, borderRadius: 8, marginBottom: 15, borderWidth: 1, borderColor: '#3B82F6' },
  aiTitle: { fontSize: 16, fontWeight: 'bold', color: '#818CF8', marginBottom: 8 },
  aiBody: { fontSize: 15, color: '#E2E8F0', lineHeight: 22 },
});