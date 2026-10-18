package com.example.jarvis.overlay

import android.view.animation.PathInterpolator

/** Material 3 emphasized curves: fast, overshoot-free entries and slightly slower exits. */
internal object Motion {
    val EnterEase = PathInterpolator(0.05f, 0.7f, 0.1f, 1f)
    val ExitEase = PathInterpolator(0.3f, 0f, 0.8f, 0.15f)
}
