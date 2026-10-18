package com.example.jarvis.device

import android.content.Context
import android.content.Intent

data class InstalledApp(val label: String, val packageName: String)

class AppLauncher(private val context: Context) {

    fun listApps(): List<InstalledApp> {
        val launcherIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return context.packageManager.queryIntentActivities(launcherIntent, 0)
            .map { InstalledApp(it.loadLabel(context.packageManager).toString(), it.activityInfo.packageName) }
            .distinctBy { it.packageName }
    }

    fun open(name: String): InstalledApp {
        val apps = listApps()
        val app = apps.firstOrNull { it.packageName == name || it.label.equals(name, ignoreCase = true) }
            ?: apps.firstOrNull { it.label.contains(name, ignoreCase = true) }
            ?: throw CommandException("appNotFound", "no installed app matches '$name'")
        val intent = context.packageManager.getLaunchIntentForPackage(app.packageName)
            ?: throw CommandException("appNotFound", "${app.label} has no launch activity")
        context.startActivity(intent)
        return app
    }
}
