package com.example.jarvis.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent

class TaskNotification(private val context: Context) {
    private val manager = context.getSystemService(NotificationManager::class.java)

    fun show(title: String = "Jarvis is controlling the phone") {
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Running task", NotificationManager.IMPORTANCE_LOW)
        )
        val stopIntent = PendingIntent.getBroadcast(
            context, 0, Intent(ACTION_STOP).setPackage(context.packageName), PendingIntent.FLAG_IMMUTABLE
        )
        val stopAction = Notification.Action.Builder(null, "Stop", stopIntent).build()
        val showIntent = PendingIntent.getBroadcast(
            context, 1, Intent(ACTION_SHOW).setPackage(context.packageName), PendingIntent.FLAG_IMMUTABLE
        )
        val notification = Notification.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_media_pause)
            .setContentTitle(title)
            .setContentText("Tap to show assistant")
            .setContentIntent(showIntent)
            .setOngoing(true)
            .addAction(stopAction)
            .build()
        manager.notify(NOTIFICATION_ID, notification)
    }

    fun hide() = manager.cancel(NOTIFICATION_ID)

    companion object {
        const val ACTION_STOP = "com.example.jarvis.STOP_TASK"
        const val ACTION_SHOW = "com.example.jarvis.SHOW_ASSISTANT"
        private const val CHANNEL_ID = "task"
        private const val NOTIFICATION_ID = 1
    }
}
