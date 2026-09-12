package com.moneybook.app

import android.content.Context
import android.content.Intent
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject

/**
 * 通知队列：通知监听服务把原文塞进来，网页那边每几秒取走一次。
 * 用 SharedPreferences 存，App 被杀也不丢。
 */
object NotifyQueue {

    private const val PREFS = "moneybook_queue_v1"
    private const val KEY = "items"
    private const val MAX = 200

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun read(ctx: Context): JSONArray {
        val raw = prefs(ctx).getString(KEY, "[]") ?: "[]"
        return try {
            JSONArray(raw)
        } catch (e: Exception) {
            JSONArray()
        }
    }

    private fun write(ctx: Context, arr: JSONArray) {
        prefs(ctx).edit().putString(KEY, arr.toString()).apply()
    }

    @Synchronized
    fun add(ctx: Context, text: String, pkg: String) {
        if (text.isBlank()) return
        val arr = read(ctx)
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i)
            if (o != null && o.optString("text") == text) return   // 同一条通知重复推送时只留一份
        }
        val item = JSONObject()
        item.put("text", text)
        item.put("pkg", pkg)
        item.put("ts", System.currentTimeMillis())
        arr.put(item)
        while (arr.length() > MAX) {
            arr.remove(0)
        }
        write(ctx, arr)
    }

    /** 取走并清空。 */
    @Synchronized
    fun drain(ctx: Context): JSONArray {
        val arr = read(ctx)
        if (arr.length() > 0) write(ctx, JSONArray())
        return arr
    }

    @Synchronized
    fun size(ctx: Context): Int = read(ctx).length()

    /** 用户是否已授予「通知使用权」。 */
    fun hasAccess(ctx: Context): Boolean {
        val flat = Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners") ?: return false
        return flat.contains(ctx.packageName)
    }

    fun openSettings(ctx: Context) {
        val i = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        i.flags = Intent.FLAG_ACTIVITY_NEW_TASK
        try {
            ctx.startActivity(i)
        } catch (e: Exception) {
            // 个别 ROM 没有这个页面
        }
    }
}
