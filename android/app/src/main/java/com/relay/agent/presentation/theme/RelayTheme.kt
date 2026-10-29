@file:Suppress("ktlint:standard:function-naming")

package com.relay.agent.presentation.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/** Relay's operator-console palette (frontend/src/styles.css) as Material 3 roles. */
private val LightColors: ColorScheme = lightColorScheme(
    primary = Color(0xFF103E46),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFDCE7E2),
    onPrimaryContainer = Color(0xFF103E46),
    secondary = Color(0xFF185763),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFEAF0ED),
    onSecondaryContainer = Color(0xFF162D31),
    tertiary = Color(0xFFA95028),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFFAE8DA),
    onTertiaryContainer = Color(0xFF6B2F14),
    background = Color(0xFFF4F6F3),
    onBackground = Color(0xFF162D31),
    surface = Color(0xFFF4F6F3),
    onSurface = Color(0xFF162D31),
    surfaceVariant = Color(0xFFEAF0ED),
    onSurfaceVariant = Color(0xFF52676B),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFF9FAF7),
    surfaceContainer = Color(0xFFFFFFFF),
    surfaceContainerHigh = Color(0xFFEAF0ED),
    surfaceContainerHighest = Color(0xFFDCE7E2),
    outline = Color(0xFF9DAFAA),
    outlineVariant = Color(0xFFCBD7D2),
    error = Color(0xFFA83E32),
    onError = Color.White,
    errorContainer = Color(0xFFFBE7E3),
    onErrorContainer = Color(0xFF6E2017)
)

private val DarkColors: ColorScheme = darkColorScheme(
    primary = Color(0xFF67C8B4),
    onPrimary = Color(0xFF0A1C20),
    primaryContainer = Color(0xFF214C55),
    onPrimaryContainer = Color(0xFFDCEFE8),
    secondary = Color(0xFFB3C5C3),
    onSecondary = Color(0xFF0D1B1E),
    secondaryContainer = Color(0xFF254147),
    onSecondaryContainer = Color(0xFFEDF5F1),
    tertiary = Color(0xFFEDA06D),
    onTertiary = Color(0xFF3A2216),
    tertiaryContainer = Color(0xFF4B3025),
    onTertiaryContainer = Color(0xFFFAE8DA),
    background = Color(0xFF0D1B1E),
    onBackground = Color(0xFFEDF5F1),
    surface = Color(0xFF0D1B1E),
    onSurface = Color(0xFFEDF5F1),
    surfaceVariant = Color(0xFF1C3438),
    onSurfaceVariant = Color(0xFFB3C5C3),
    surfaceContainerLowest = Color(0xFF0A1619),
    surfaceContainerLow = Color(0xFF112327),
    surfaceContainer = Color(0xFF162B2F),
    surfaceContainerHigh = Color(0xFF1C3438),
    surfaceContainerHighest = Color(0xFF254147),
    outline = Color(0xFF567078),
    outlineVariant = Color(0xFF355157),
    error = Color(0xFFFF9C8C),
    onError = Color(0xFF3D1410),
    errorContainer = Color(0xFF4B2727),
    onErrorContainer = Color(0xFFFBE7E3)
)

/** Colours Material 3 has no role for: the header band and collector states. */
@Immutable
data class RelayColors(
    val header: Color,
    val onHeader: Color,
    val onHeaderSoft: Color,
    val headerRule: Color,
    val waiting: Color,
    val dropped: Color,
    val success: Color,
    val successContainer: Color,
    val warning: Color,
    val warningContainer: Color
)

private val LightRelay = RelayColors(
    header = Color(0xFF103E46),
    onHeader = Color(0xFFF4F6F3),
    onHeaderSoft = Color(0xFFB8CCC8),
    headerRule = Color(0xFF2B5A62),
    waiting = Color(0xFFF2A97A),
    dropped = Color(0xFFFF9C8C),
    success = Color(0xFF1E6C58),
    successContainer = Color(0xFFDCEFE8),
    warning = Color(0xFF8C531D),
    warningContainer = Color(0xFFF9E7CD)
)

private val DarkRelay = RelayColors(
    header = Color(0xFF143238),
    onHeader = Color(0xFFEDF5F1),
    onHeaderSoft = Color(0xFFA3BAB7),
    headerRule = Color(0xFF2E5258),
    waiting = Color(0xFFEDA06D),
    dropped = Color(0xFFFF9C8C),
    success = Color(0xFF79D5B5),
    successContainer = Color(0xFF21443B),
    warning = Color(0xFFF4BF76),
    warningContainer = Color(0xFF493823)
)

val LocalRelayColors = staticCompositionLocalOf { LightRelay }

private val RelayTypography = Typography().run {
    copy(
        displayMedium = displayMedium.copy(
            fontWeight = FontWeight.SemiBold,
            fontFeatureSettings = "tnum"
        ),
        titleLarge = titleLarge.copy(fontWeight = FontWeight.SemiBold),
        titleMedium = titleMedium.copy(fontWeight = FontWeight.SemiBold),
        labelLarge = labelLarge.copy(fontWeight = FontWeight.SemiBold)
    )
}

private val RelayShapes = Shapes(
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp)
)

@Composable
fun RelayTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalRelayColors provides if (dark) DarkRelay else LightRelay) {
        MaterialTheme(
            colorScheme = if (dark) DarkColors else LightColors,
            typography = RelayTypography,
            shapes = RelayShapes,
            content = content
        )
    }
}
