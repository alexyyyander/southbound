package io.southbound.endpoint;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** HTTP is a replaceable transport; command names and receipts are terminal v1. */
final class ProtocolClient {
    static final class HttpError extends Exception {
        final int status;
        HttpError(int status, String message) { super(message); this.status = status; }
    }

    static String normalizeUrl(String value) throws Exception {
        URI uri = new URI(value.trim());
        boolean https = "https".equalsIgnoreCase(uri.getScheme());
        boolean debugHttp = BuildConfig.DEBUG && "http".equalsIgnoreCase(uri.getScheme());
        if ((!https && !debugHttp) || uri.getHost() == null || uri.getUserInfo() != null
                || uri.getQuery() != null || uri.getFragment() != null
                || !(uri.getPath().isEmpty() || "/".equals(uri.getPath()))) {
            throw new IllegalArgumentException("请输入服务根地址；正式版要求 HTTPS");
        }
        return value.trim().replaceAll("/+$", "");
    }

    static JSONObject post(String baseUrl, String path, String token, JSONObject body) throws Exception {
        return request(baseUrl,path,token,body.toString().getBytes(StandardCharsets.UTF_8),"application/json","");
    }

    static JSONObject uploadImage(String baseUrl,String token,String commandId,byte[] data) throws Exception {
        return request(baseUrl,"/api/terminal/images",token,data,"image/jpeg",commandId);
    }

    private static JSONObject request(String baseUrl,String path,String token,byte[] bytes,String contentType,String commandId) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(baseUrl + path).openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(7000);
            connection.setReadTimeout(10000);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", contentType);
            if (!commandId.isEmpty()) connection.setRequestProperty("X-Command-Id",commandId);
            if (!token.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setFixedLengthStreamingMode(bytes.length);
            connection.setDoOutput(true);
            try (var out = connection.getOutputStream()) { out.write(bytes); }
            int status = connection.getResponseCode();
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            if (stream != null) try (stream) {
                byte[] chunk = new byte[4096];
                for (int n; (n = stream.read(chunk)) >= 0;) {
                    if (buffer.size() + n > 65536) throw new IllegalStateException("response too large");
                    buffer.write(chunk, 0, n);
                }
            }
            if (status < 200 || status >= 300) throw new HttpError(status, "HTTP " + status);
            return new JSONObject(buffer.toString(StandardCharsets.UTF_8.name()));
        } finally { connection.disconnect(); }
    }
}
