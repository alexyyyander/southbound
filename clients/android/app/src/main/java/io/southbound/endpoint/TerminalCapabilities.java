package io.southbound.endpoint;

import org.json.JSONObject;

final class TerminalCapabilities {
    static JSONObject supported() throws Exception {
        return new JSONObject().put("notify",1).put("speak",1).put("read_battery",1)
                .put("show_message",1).put("device_info",1).put("capture_screen",1)
                .put("ui.observe",1).put("app.launch",1).put("ui.tap",1)
                .put("ui.input",1).put("ui.swipe",1).put("ui.back",1);
    }
}
