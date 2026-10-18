package com.example.jarvis.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import com.example.jarvis.ui.BrandColors

private val EmberColors = darkColorScheme(
    primary = Color(BrandColors.Orange),
    onPrimary = Color(BrandColors.Background),
    primaryContainer = Color(BrandColors.SurfaceRaised),
    onPrimaryContainer = Color(BrandColors.Text),
    secondary = Color(BrandColors.Red),
    onSecondary = Color(BrandColors.Background),
    tertiary = Color(BrandColors.Amber),
    background = Color(BrandColors.Background),
    onBackground = Color(BrandColors.Text),
    surface = Color(BrandColors.Surface),
    onSurface = Color(BrandColors.Text),
    surfaceVariant = Color(BrandColors.SurfaceRaised),
    onSurfaceVariant = Color(BrandColors.Muted),
    outline = Color(BrandColors.Border),
    error = Color(BrandColors.Red),
)

@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = EmberColors, typography = Typography, content = content)
}
