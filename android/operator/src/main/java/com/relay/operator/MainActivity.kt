package com.relay.operator

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.webkit.CookieManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.core.graphics.toColorInt
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.fragment.app.FragmentActivity
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/** The operator portal as an app: one WebView kept to the Relay server. */
class MainActivity : FragmentActivity() {
    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var offline: View
    private val portal: Uri = (BuildConfig.RELAY_BASE_URL + "operator/").toUri()

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        web = WebView(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.userAgentString += " RelayOperator/" + BuildConfig.VERSION_NAME
            addJavascriptInterface(FileBridge(this@MainActivity), "RelayAndroid")
            addJavascriptInterface(FingerprintLogin(this@MainActivity, this), "RelayAuth")
            webViewClient = PortalClient()
        }
        CookieManager.getInstance().setAcceptCookie(true)
        refresh = SwipeRefreshLayout(this).apply {
            setColorSchemeColors("#E2934D".toColorInt())
            setOnChildScrollUpCallback { _, _ -> web.scrollY > 0 }
            setOnRefreshListener { web.reload() }
            addView(web, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        }
        offline = offlineView()
        val root = FrameLayout(this).apply {
            addView(refresh, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
            addView(offline, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        }
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime()
            )
            view.updatePadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setContentView(root)
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (web.canGoBack()) {
                        web.goBack()
                    } else {
                        finish()
                    }
                }
            }
        )
        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(portal.toString())
        }
        if (savedInstanceState == null) UpdateCheck.run(this)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()
    }

    private fun offlineView(): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        visibility = View.GONE
        setBackgroundColor(getColor(R.color.surface))
        val pad = (24 * resources.displayMetrics.density).toInt()
        setPadding(pad, pad, pad, pad)
        addView(
            TextView(context).apply {
                setText(R.string.offline_title)
                textSize = 22f
                setTextColor(getColor(R.color.brand))
                gravity = Gravity.CENTER
            }
        )
        addView(
            TextView(context).apply {
                setText(R.string.offline_body)
                textSize = 16f
                gravity = Gravity.CENTER
                setPadding(0, pad / 2, 0, pad)
            }
        )
        addView(
            Button(context).apply {
                setText(R.string.retry)
                setOnClickListener {
                    offline.visibility = View.GONE
                    refresh.isRefreshing = true
                    web.reload()
                }
            },
            LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT)
        )
    }

    private inner class PortalClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            if (url.scheme == portal.scheme && url.host == portal.host) return false
            // Anything else (help links, mail) opens outside the app.
            try {
                startActivity(Intent(Intent.ACTION_VIEW, url))
            } catch (_: ActivityNotFoundException) {
                Unit
            }
            return true
        }

        override fun onPageFinished(view: WebView, url: String?) {
            refresh.isRefreshing = false
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError
        ) {
            if (request.isForMainFrame) {
                refresh.isRefreshing = false
                offline.visibility = View.VISIBLE
            }
        }
    }
}
