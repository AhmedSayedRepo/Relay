# The portal calls these from JavaScript (saving Excel exports).
-keepclassmembers class com.relay.operator.FileBridge {
    @android.webkit.JavascriptInterface <methods>;
}
-keepclassmembers class com.relay.operator.FingerprintLogin {
    @android.webkit.JavascriptInterface <methods>;
}
