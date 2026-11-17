# The portal calls these from JavaScript (saving Excel exports).
-keepclassmembers class com.relay.operator.FileBridge {
    @android.webkit.JavascriptInterface <methods>;
}
