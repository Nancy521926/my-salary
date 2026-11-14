package com.yili.payslip;

import android.Manifest;
import android.app.Activity;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.print.PrintAttributes;
import android.print.PrintDocumentAdapter;
import android.print.PrintManager;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * 工资条生成器 —— 原生外壳。
 * 界面全部在 assets/index.html 中，这里只负责：
 *   1. 承载 WebView（用固定 origin 加载，保证 localStorage 可用）
 *   2. 把页面画出来的工资条 PNG 存进手机相册（Pictures/工资条）
 *   3. 调起系统打印（可另存为 PDF）
 */
public class MainActivity extends Activity {

    private static final String BASE_URL = "https://app.payslip.local/";
    private static final String ALBUM_DIR = "工资条";
    private static final int REQ_STORAGE = 1001;

    private WebView webView;
    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        webView.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        webView.setBackgroundColor(0xFFEEF2F8);
        configureWebView();
        setContentView(webView);

        webView.addJavascriptInterface(new Bridge(), "AndroidApp");
        loadApp();

        requestLegacyStorageIfNeeded();
    }

    @SuppressWarnings("deprecation")
    private void configureWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setTextZoom(100);
        s.setUseWideViewPort(false);
        s.setLoadWithOverviewMode(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            s.setSafeBrowsingEnabled(false);
        }
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // 页面内无外链，出现即视为异常跳转，一律拦下
                return !url.startsWith(BASE_URL);
            }
        });
        webView.setWebChromeClient(new WebChromeClient());
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);
    }

    private void loadApp() {
        try {
            InputStream in = getAssets().open("index.html");
            byte[] buf = new byte[in.available()];
            int read = in.read(buf);
            in.close();
            String html = new String(buf, 0, Math.max(read, 0), StandardCharsets.UTF_8);
            // 用固定 origin 加载，localStorage 才能稳定落盘
            webView.loadDataWithBaseURL(BASE_URL, html, "text/html", "utf-8", null);
        } catch (IOException e) {
            Toast.makeText(this, "加载失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    /** Android 6～9 需要运行时申请写外部存储权限 */
    private void requestLegacyStorageIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
            if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                    != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(
                        new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_STORAGE && results.length > 0
                && results[0] != PackageManager.PERMISSION_GRANTED) {
            toast("未授予存储权限，保存的图片将无法写入相册");
        }
    }

    private void toast(final String msg) {
        ui.post(new Runnable() {
            @Override public void run() {
                Toast.makeText(MainActivity.this, msg, Toast.LENGTH_LONG).show();
            }
        });
    }

    @Override
    public void onBackPressed() {
        // 让页面先处理（关闭编辑层），处理不了再退出
        webView.evaluateJavascript(
                "(function(){var s=document.getElementById('sheet');" +
                "if(s&&s.classList.contains('open')){" +
                "var c=document.getElementById('btnCancel');if(c){c.click();return '1';}}return '0';})()",
                new android.webkit.ValueCallback<String>() {
                    @Override public void onReceiveValue(String value) {
                        if (value == null || !value.contains("1")) {
                            finish();
                        }
                    }
                });
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.removeJavascriptInterface("AndroidApp");
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    /* ================= JS 桥 ================= */
    private class Bridge {

        /** 把页面画出来的工资条 PNG 存进相册（Pictures/工资条）。 */
        @JavascriptInterface
        public String saveImage(String name, String base64) {
            try {
                byte[] data = Base64.decode(base64, Base64.DEFAULT);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    return saveImageScoped(name, data);
                }
                return saveImageLegacy(name, data);
            } catch (Exception e) {
                final String msg = "保存失败：" + e.getMessage();
                ui.post(new Runnable() {
                    @Override public void run() {
                        Toast.makeText(MainActivity.this, msg, Toast.LENGTH_LONG).show();
                    }
                });
                return null;
            }
        }

        @JavascriptInterface
        public void printPage() {
            ui.post(new Runnable() {
                @Override public void run() {
                    PrintManager pm = (PrintManager) getSystemService(Context.PRINT_SERVICE);
                    if (pm == null) { return; }
                    String job = "工资条";
                    PrintDocumentAdapter adapter = webView.createPrintDocumentAdapter(job);
                    PrintAttributes attrs = new PrintAttributes.Builder()
                            .setMediaSize(PrintAttributes.MediaSize.ISO_A4.asLandscape())
                            .setResolution(new PrintAttributes.Resolution("payslip", "payslip", 600, 600))
                            .setMinMargins(PrintAttributes.Margins.NO_MARGINS)
                            .build();
                    pm.print(job, adapter, attrs);
                }
            });
        }
    }

    /** Android 10+：走 MediaStore，落 Pictures/工资条，相册里能直接看到 */
    private String saveImageScoped(String name, byte[] data) throws IOException {
        ContentValues cv = new ContentValues();
        cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        cv.put(MediaStore.MediaColumns.MIME_TYPE, "image/png");
        cv.put(MediaStore.MediaColumns.RELATIVE_PATH,
                Environment.DIRECTORY_PICTURES + "/" + ALBUM_DIR);
        cv.put(MediaStore.MediaColumns.IS_PENDING, 1);

        Uri uri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
        if (uri == null) throw new IOException("无法在相册创建文件");
        OutputStream os = getContentResolver().openOutputStream(uri);
        if (os == null) throw new IOException("无法打开输出流");
        try {
            os.write(data);
            os.flush();
        } finally {
            os.close();
        }

        ContentValues done = new ContentValues();
        done.put(MediaStore.MediaColumns.IS_PENDING, 0);
        getContentResolver().update(uri, done, null, null);

        toast("已保存到相册：" + ALBUM_DIR + "/" + name);
        return uri.toString();
    }

    /** Android 9 及以下：写公共 Pictures 目录，再广播让相册收录 */
    @SuppressWarnings("deprecation")
    private String saveImageLegacy(String name, byte[] data) throws IOException {
        File pictures = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES);
        if (pictures == null) throw new IOException("未找到相册目录");
        File dir = new File(pictures, ALBUM_DIR);
        if (!dir.exists() && !dir.mkdirs()) throw new IOException("无法创建相册目录");
        File target = new File(dir, name);
        FileOutputStream fos = new FileOutputStream(target);
        try {
            fos.write(data);
            fos.flush();
        } finally {
            fos.close();
        }
        Intent scan = new Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE, Uri.fromFile(target));
        sendBroadcast(scan);
        toast("已保存到相册：" + ALBUM_DIR + "/" + name);
        return target.getAbsolutePath();
    }
}
