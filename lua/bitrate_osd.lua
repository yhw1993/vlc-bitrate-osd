--[[
    Bitrate OSD - VLC Extension v4.0
    OSD overlay for real-time video stream bitrate statistics

    v4.0 changes:
    - HTTP API fallback: when vlc.var.get(input,"stats") returns nil,
      automatically fetches stats via VLC HTTP interface using vlc.net
    - Added base64 encoder for HTTP Basic Auth
    - Added XML parser for HTTP API response
    - Detailed debug shows which method is active (vlc_api / http_api)

    v3.0 changes:
    - Removed capabilities={"menu"} so clicking directly opens dialog
    - Added pcall error protection on all critical functions
    - Simplified dialog layout for reliability
    - Added byte-delta bitrate calculation
    - VLC demux_bitrate unit auto-detection (MB/s vs B/s)

    Install: %APPDATA%\vlc\lua\extensions\bitrate_osd.lua
    Usage: VLC -> View -> Bitrate OSD
    Requires: VLC started with --extraintf http --http-port 8080 --http-password vlc123
--]]

-- ============================================================
-- Globals
-- ============================================================
local dlg = nil
local timer = nil
local stats_label = nil
local debug_label = nil
local osd_enabled = false
local marq_enabled = false
local bitrate_history = {}
local MAX_HISTORY = 60
local UPDATE_INTERVAL = 500
local update_count = 0
local last_error = "none"
local has_input = false
local has_stats = false
local prev_demux_bytes = nil

-- HTTP API config (fallback when vlc.var.get stats is nil)
local HTTP_HOST = "127.0.0.1"
local HTTP_PORT = 8080
local HTTP_PASS = "vlc123"
local stats_method = "unknown"  -- will be "vlc_api" or "http_api"

-- ============================================================
-- Descriptor (NO menu capability = direct dialog on click)
-- ============================================================
function descriptor()
    return {
        title = "Bitrate OSD",
        version = "4.0",
        author = "WorkBuddy",
        url = '',
        shortdesc = "Bitrate Statistics OSD",
        description = "Real-time video stream bitrate overlay display"
    }
end

-- ============================================================
-- Activate
-- ============================================================
function activate()
    local ok, err = pcall(function()
        bitrate_history = {}
        update_count = 0
        last_error = "none"
        prev_demux_bytes = nil
        create_dialog()
        timer = vlc.timer(UPDATE_INTERVAL, update_loop)
        update_debug("Extension activated, timer started (" .. UPDATE_INTERVAL .. "ms)")
    end)
    if not ok then
        -- If dialog creation fails, try to show error via OSD
        vlc.msg.err("Bitrate OSD activate error: " .. tostring(err))
        last_error = tostring(err)
    end
end

-- ============================================================
-- Create dialog
-- ============================================================
function create_dialog()
    dlg = vlc.dialog("Bitrate OSD v4.0")

    dlg:add_label("=== Bitrate Statistics OSD v3.0 ===", 1, 1, 4, 1)

    -- Control buttons
    dlg:add_button("Toggle OSD Message", toggle_osd, 1, 2, 2, 1)
    dlg:add_button("Toggle Marquee Overlay", toggle_marquee, 3, 2, 2, 1)
    dlg:add_button("Refresh Stats Now", manual_update, 1, 3, 2, 1)
    dlg:add_button("Clear History", clear_history, 3, 3, 2, 1)

    dlg:add_label("------------------------------------------------------------", 1, 4, 4, 1)

    -- Stats area
    dlg:add_label("Real-time Statistics:", 1, 5, 4, 1)
    stats_label = dlg:add_label("Waiting for video playback...\n\nPlease open and play a video file in VLC first.", 1, 6, 4, 8)

    dlg:add_label("------------------------------------------------------------", 1, 14, 4, 1)

    -- Debug area
    dlg:add_label("Debug Info:", 1, 15, 4, 1)
    debug_label = dlg:add_label("Debug: initializing...", 1, 16, 4, 4)

    dlg:add_label("Tip: OSD=brief message | Marquee=persistent overlay (recommended) | Refresh=get data now", 1, 20, 4, 1)
    dlg:add_label("Note: If Stats=no, make sure VLC runs with: --extraintf http --http-port 8080 --http-password vlc123", 1, 21, 4, 1)
end

-- ============================================================
-- Update debug info
-- ============================================================
function update_debug(msg)
    last_error = msg
    local status = string.format(
        "Timer ticks: %d | Input: %s | Stats: %s | Method: %s\nStatus: %s",
        update_count,
        has_input and "yes" or "no",
        has_stats and "yes" or "no",
        stats_method,
        msg
    )
    if debug_label then
        debug_label:set_text(status)
    end
end

-- ============================================================
-- Manual refresh
-- ============================================================
function manual_update()
    update_debug("Manual refresh...")
    update_loop()
end

-- ============================================================
-- Clear history
-- ============================================================
function clear_history()
    bitrate_history = {}
    prev_demux_bytes = nil
    update_debug("History cleared")
end

-- ============================================================
-- Toggle OSD message (brief)
-- ============================================================
function toggle_osd()
    osd_enabled = not osd_enabled
    if osd_enabled then
        vlc.osd.message(">>> Bitrate OSD ON <<<", 1)
        update_debug("OSD enabled")
    else
        vlc.osd.message(">>> Bitrate OSD OFF <<<", 1)
        update_debug("OSD disabled")
    end
end

-- ============================================================
-- Toggle Marquee overlay (persistent - recommended)
-- ============================================================
function toggle_marquee()
    marq_enabled = not marq_enabled

    if marq_enabled then
        local input = vlc.object.input()
        if not input then
            update_debug("Marquee: no playback detected, play a video first")
            vlc.osd.message("Play a video first", 1)
            marq_enabled = false
            return
        end

        -- Set marquee parameters
        vlc.var.set(input, "marq-marquee", "Bitrate loading...")
        vlc.var.set(input, "marq-position", 4)       -- top-left
        vlc.var.set(input, "marq-color", 16776960)    -- yellow (0xFFFF00)
        vlc.var.set(input, "marq-opacity", 255)
        vlc.var.set(input, "marq-size", 16)
        vlc.var.set(input, "marq-timeout", 0)         -- never timeout
        vlc.var.set(input, "marq-refresh", 500)       -- 500ms refresh

        -- Add marq as sub-source
        local current = vlc.var.get(input, "sub-source") or ""
        if current == "" then
            vlc.var.set(input, "sub-source", "marq")
        elseif not string.find(current, "marq") then
            vlc.var.set(input, "sub-source", current .. ":marq")
        end

        vlc.var.set(input, "marq-marquee", "Bitrate overlay active, waiting for data...")
        vlc.osd.message(">>> Marquee ON <<<", 1)
        update_debug("Marquee enabled, sub-source=" .. (vlc.var.get(input, "sub-source") or "?"))
    else
        local input = vlc.object.input()
        if input then
            local current = vlc.var.get(input, "sub-source") or ""
            current = string.gsub(current, "marq:?", "")
            current = string.gsub(current, ":$", "")
            current = string.gsub(current, "^:", "")
            vlc.var.set(input, "sub-source", current)
        end
        vlc.osd.message(">>> Marquee OFF <<<", 1)
        update_debug("Marquee disabled")
    end
end

-- ============================================================
-- Format bitrate (bytes/s -> readable)
-- ============================================================
function format_bitrate(bytes_per_sec)
    if not bytes_per_sec or bytes_per_sec <= 0 then
        return "0 bps"
    end
    local bits_per_sec = bytes_per_sec * 8
    if bits_per_sec >= 1000000000 then
        return string.format("%.2f Gbps", bits_per_sec / 1000000000)
    elseif bits_per_sec >= 1000000 then
        return string.format("%.2f Mbps", bits_per_sec / 1000000)
    elseif bits_per_sec >= 1000 then
        return string.format("%.1f kbps", bits_per_sec / 1000)
    else
        return string.format("%d bps", math.floor(bits_per_sec))
    end
end

-- ============================================================
-- Format bytes
-- ============================================================
function format_bytes(bytes)
    if not bytes then return "0 B" end
    if bytes >= 1073741824 then
        return string.format("%.2f GB", bytes / 1073741824)
    elseif bytes >= 1048576 then
        return string.format("%.1f MB", bytes / 1048576)
    elseif bytes >= 1024 then
        return string.format("%.1f KB", bytes / 1024)
    else
        return string.format("%d B", bytes)
    end
end

-- ============================================================
-- Bitrate bar
-- ============================================================
function bitrate_bar(bytes_per_sec, max_br)
    if not bytes_per_sec or bytes_per_sec <= 0 then
        return "[          ]"
    end
    local ratio = 0
    if max_br and max_br > 0 then
        ratio = math.min(bytes_per_sec / max_br, 1.0)
    end
    local filled = math.floor(ratio * 10)
    local bar = ""
    for i = 1, 10 do
        if i <= filled then
            bar = bar .. "#"
        else
            bar = bar .. " "
        end
    end
    return "[" .. bar .. "]"
end

-- ============================================================
-- Safe get stats field
-- ============================================================
function safe_get(stats, field)
    if not stats then return 0 end
    local val = stats[field]
    if type(val) ~= "number" then return 0 end
    return val
end

-- ============================================================
-- Base64 encoder (for HTTP Basic Auth)
-- ============================================================
local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
function base64_encode(data)
    if not data then return "" end
    local result = {}
    local i = 1
    while i <= #data do
        local a, b, c = string.byte(data, i), string.byte(data, i+1), string.byte(data, i+2)
        local padding = 0
        if not b then b = 0; padding = 2
        elseif not c then c = 0; padding = 1 end
        local n = a * 65536 + b * 256 + c
        local d1 = math.floor(n / 262144) % 64 + 1
        local d2 = math.floor(n / 4096) % 64 + 1
        local d3 = math.floor(n / 64) % 64 + 1
        local d4 = n % 64 + 1
        table.insert(result, string.sub(B64, d1, d1))
        table.insert(result, string.sub(B64, d2, d2))
        if padding < 2 then table.insert(result, string.sub(B64, d3, d3)) else table.insert(result, "=") end
        if padding < 1 then table.insert(result, string.sub(B64, d4, d4)) else table.insert(result, "=") end
        i = i + 3
    end
    return table.concat(result)
end

-- ============================================================
-- Extract value from XML by tag name
-- ============================================================
function xml_value(xml, tag)
    if not xml then return nil end
    local val = string.match(xml, "<" .. tag .. ">([^<]*)</" .. tag .. ">")
    if val then
        local num = tonumber(val)
        if num then return num end
        return val
    end
    return nil
end

-- ============================================================
-- Fetch stats via VLC HTTP API (fallback for vlc.var.get)
-- Uses vlc.net which is available in VLC's Lua environment
-- ============================================================
function fetch_stats_http()
    local ok, err = pcall(function()
        -- Try vlc.net (available in VLC Lua)
        local fd = vlc.net.connect_tcp(HTTP_HOST, HTTP_PORT)
        if not fd then return nil end

        local auth = base64_encode(":" .. HTTP_PASS)
        local request = "GET /requests/status.xml HTTP/1.0\r\n" ..
                        "Host: " .. HTTP_HOST .. ":" .. HTTP_PORT .. "\r\n" ..
                        "Authorization: Basic " .. auth .. "\r\n" ..
                        "Connection: close\r\n\r\n"
        vlc.net.send(fd, request)

        -- Read response
        local response = ""
        local chunk = vlc.net.recv(fd, 8192)
        local retries = 0
        while chunk and #chunk > 0 and retries < 50 do
            response = response .. chunk
            chunk = vlc.net.recv(fd, 8192)
            retries = retries + 1
        end
        vlc.net.close(fd)

        return parse_stats_xml(response)
    end)
    if not ok then return nil end
    return err
end

-- ============================================================
-- Parse stats from XML string (shared by http and curl methods)
-- ============================================================
function parse_stats_xml(response)
    if not response or #response == 0 then return nil end

    -- Find XML body (skip HTTP headers)
    local xml_start = string.find(response, "<root")
    if not xml_start then return nil end
    local xml = string.sub(response, xml_start)

    local stats = {}
    stats.demux_bitrate = xml_value(xml, "demuxbitrate") or 0
    stats.input_bitrate = xml_value(xml, "inputbitrate") or 0
    stats.read_bytes = xml_value(xml, "readbytes") or 0
    stats.demux_read_bytes = xml_value(xml, "demuxreadbytes") or 0
    stats.displayed_pictures = xml_value(xml, "displayedpictures") or 0
    stats.lost_pictures = xml_value(xml, "lostpictures") or 0
    stats.late_pictures = xml_value(xml, "latepictures") or 0
    stats.played_abuffers = xml_value(xml, "playedabuffers") or 0
    stats.lost_abuffers = xml_value(xml, "lostabuffers") or 0
    stats.discontinuities = xml_value(xml, "discontinuities") or 0
    stats.decoded_video = xml_value(xml, "decodedvideo") or 0
    stats._state = xml_value(xml, "state") or "unknown"
    stats._position = xml_value(xml, "position") or 0
    stats._length = xml_value(xml, "length") or 0

    return stats
end

-- ============================================================
-- Fetch stats via curl (third fallback - uses io.popen)
-- ============================================================
function fetch_stats_curl()
    local ok, result = pcall(function()
        local auth = base64_encode(":" .. HTTP_PASS)
        local cmd = 'curl -s -H "Authorization: Basic ' .. auth .. '" ' ..
                    'http://' .. HTTP_HOST .. ':' .. HTTP_PORT .. '/requests/status.xml 2>nul'
        local f = io.popen(cmd)
        if not f then return nil end
        local response = f:read("*a")
        f:close()
        return parse_stats_xml(response)
    end)
    if not ok then return nil end
    return result
end

-- ============================================================
-- Main update loop (called by timer)
-- ============================================================
function update_loop()
    local ok, err = pcall(function()
    update_count = update_count + 1

    local input = vlc.object.input()
    if not input then
        has_input = false
        has_stats = false
        if stats_label then
            stats_label:set_text("No playback detected\n\nPlease open and play a video file in VLC\n\nTimer ticks: " .. update_count)
        end
        if update_count <= 3 or update_count % 20 == 0 then
            update_debug("No input (playback not started?)")
        end
        return
    end

    has_input = true

    -- ========== Get statistics ==========
    -- Method 1: Try vlc.var.get(input, "stats") - native VLC Lua API
    local stats = vlc.var.get(input, "stats")

    if stats and type(stats) == "table" then
        stats_method = "vlc_api"
    else
        -- Method 2: Fallback to HTTP API via vlc.net
        stats = fetch_stats_http()
        if stats then
            stats_method = "http_api"
        else
            -- Method 3: Fallback to curl via io.popen
            stats = fetch_stats_curl()
            if stats then
                stats_method = "curl_api"
            else
                has_stats = false
                if stats_label then
                    stats_label:set_text("Playback detected but cannot get stats\n\n" ..
                        "vlc.var.get returned: " .. type(vlc.var.get(input, "stats")) .. "\n" ..
                        "HTTP API (vlc.net) failed\n" ..
                        "curl API (io.popen) failed\n\n" ..
                        "Make sure VLC started with:\n" ..
                        "  --extraintf http --http-port 8080 --http-password vlc123\n\n" ..
                        "Timer ticks: " .. update_count)
                end
                update_debug("All 3 methods failed: vlc_api, http_api, curl_api")
                return
            end
        end
    end

    has_stats = true

    -- Read stats fields safely
    local vlc_demux_br = safe_get(stats, "demux_bitrate")
    local vlc_input_br = safe_get(stats, "input_bitrate")
    local displayed = safe_get(stats, "displayed_pictures")
    local lost = safe_get(stats, "lost_pictures")
    local late = safe_get(stats, "late_pictures")
    local played_a = safe_get(stats, "played_abuffers")
    local lost_a = safe_get(stats, "lost_abuffers")
    local read_bytes = safe_get(stats, "read_bytes")
    local demux_bytes = safe_get(stats, "demux_read_bytes")
    local discontinuities = safe_get(stats, "discontinuities")

    -- Calculate instantaneous bitrate from byte delta
    local demux_br = 0
    local input_br = vlc_input_br
    local dt = UPDATE_INTERVAL / 1000  -- ms to seconds
    if prev_demux_bytes ~= nil then
        local db = demux_bytes - prev_demux_bytes
        if db > 0 then
            demux_br = db / dt
        end
    end
    -- Fallback to VLC built-in value if delta is 0 (file fully buffered)
    -- VLC built-in value < 1000 is likely MB/s, multiply to convert
    if demux_br == 0 and vlc_demux_br > 0 then
        if vlc_demux_br < 1000 then
            demux_br = vlc_demux_br * 1048576  -- MB/s -> B/s
        else
            demux_br = vlc_demux_br
        end
    end
    prev_demux_bytes = demux_bytes

    -- Record history
    if demux_br > 0 then
        table.insert(bitrate_history, demux_br)
        if #bitrate_history > MAX_HISTORY then
            table.remove(bitrate_history, 1)
        end
    end

    -- Calculate average and peak
    local sum_br = 0
    local peak_br = 0
    for _, v in ipairs(bitrate_history) do
        sum_br = sum_br + v
        if v > peak_br then peak_br = v end
    end
    local avg_br = (#bitrate_history > 0) and (sum_br / #bitrate_history) or 0

    -- Get video info
    local vout = vlc.object.vout()
    local width, height, fps = 0, 0, 0
    if vout then
        width = vlc.var.get(vout, "width") or 0
        height = vlc.var.get(vout, "height") or 0
        fps = vlc.var.get(vout, "fps") or 0
    end

    -- Frame loss rate
    local total_frames = displayed + lost
    local loss_rate = 0
    if total_frames > 0 then
        loss_rate = (lost / total_frames) * 100
    end

    -- ========== Build dialog text ==========
    local dialog_text = string.format(
        "========================================\n" ..
        "        Video Stream Bitrate Stats\n" ..
        "========================================\n" ..
        "\n" ..
        "  Instant Bitrate:  %s  %s\n" ..
        "  Average Bitrate:  %s\n" ..
        "  Peak Bitrate:     %s\n" ..
        "  Input Bitrate:    %s  (network)\n" ..
        "\n" ..
        "----------------------------------------\n" ..
        "  Resolution:  %dx%d\n" ..
        "  Frame Rate:  %.1f fps\n" ..
        "  Displayed:   %d frames\n" ..
        "  Lost:        %d frames  (%.1f%%)\n" ..
        "  Late:        %d frames\n" ..
        "----------------------------------------\n" ..
        "  Audio Played:  %d\n" ..
        "  Audio Lost:    %d\n" ..
        "  Discontinuities: %d\n" ..
        "----------------------------------------\n" ..
        "  Total Read:  %s (input)\n" ..
        "  Total Read:  %s (demux)\n" ..
        "========================================\n" ..
        "  Samples: %d / %d  |  Ticks: %d",
        format_bitrate(demux_br), bitrate_bar(demux_br, peak_br),
        format_bitrate(avg_br),
        format_bitrate(peak_br),
        format_bitrate(input_br),
        width, height,
        fps,
        displayed,
        lost, loss_rate,
        late,
        played_a,
        lost_a,
        discontinuities,
        format_bytes(read_bytes),
        format_bytes(demux_bytes),
        #bitrate_history, MAX_HISTORY, update_count
    )

    if stats_label then
        stats_label:set_text(dialog_text)
    end

    -- ========== OSD brief display (every 5 ticks) ==========
    if osd_enabled and (update_count % 5 == 0) then
        local osd_text = string.format(
            "BR: %s | AVG: %s | %dx%d@%.0ffps | Lost: %d (%.1f%%)",
            format_bitrate(demux_br),
            format_bitrate(avg_br),
            width, height, fps,
            lost, loss_rate
        )
        vlc.osd.message(osd_text, 1)
    end

    -- ========== Marquee persistent overlay ==========
    if marq_enabled then
        local marq_text = string.format(
            "[%s] AVG:%s | %dx%d@%.0ffps | Lost:%d/%d (%.1f%%) | Disc:%d",
            format_bitrate(demux_br),
            format_bitrate(avg_br),
            width, height, fps,
            lost, total_frames, loss_rate,
            discontinuities
        )
        local cur_input = vlc.object.input()
        if cur_input then
            vlc.var.set(cur_input, "marq-marquee", marq_text)
        end
    end

    -- Periodic debug update
    if update_count <= 5 or update_count % 30 == 0 then
        update_debug(string.format("OK [%s]: demux_br=%.2f B/s, input_br=%.2f B/s, bytes=%d",
            stats_method, demux_br, input_br, demux_bytes))
    end

    end) -- pcall
    if not ok then
        if debug_label then
            debug_label:set_text("Error in update_loop: " .. tostring(err) .. " (tick " .. update_count .. ")")
        end
    end
end

-- ============================================================
-- Deactivate
-- ============================================================
function deactivate()
    if timer then
        timer:kill()
        timer = nil
    end
    osd_enabled = false

    local input = vlc.object.input()
    if input and marq_enabled then
        local current = vlc.var.get(input, "sub-source") or ""
        current = string.gsub(current, "marq:?", "")
        current = string.gsub(current, ":$", "")
        current = string.gsub(current, "^:", "")
        vlc.var.set(input, "sub-source", current)
    end
    marq_enabled = false
    bitrate_history = {}
    prev_demux_bytes = nil
end

-- ============================================================
-- Close
-- ============================================================
function close()
    deactivate()
    vlc.deactivate()
end
