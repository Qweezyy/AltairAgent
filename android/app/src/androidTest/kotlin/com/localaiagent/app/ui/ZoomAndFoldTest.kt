package com.localaiagent.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pinch
import androidx.compose.ui.test.doubleClick
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.height
import androidx.compose.ui.geometry.Offset
import androidx.compose.material3.Text
import androidx.test.platform.app.InstrumentationRegistry
import com.localaiagent.app.R
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Real gestures on a device: zooming photos/videos and folding long messages. */
class ZoomAndFoldTest {

    @get:Rule
    val rule = createComposeRule()

    private fun str(id: Int) = InstrumentationRegistry.getInstrumentation().targetContext.getString(id)

    // A black frame with a white 40dp square in the middle: zooming in makes the square grow, so a
    // pixel just outside it turns white. (Layout bounds would not show it: the frame clips.)
    private fun zoomable() {
        rule.setContent {
            ZoomBox(Modifier.size(300.dp).background(Color.Black).testTag("frame")) {
                Box(Modifier.size(40.dp).background(Color.White))
            }
        }
    }

    /** Whether the pixel [dx] dp right of the frame's center is white. */
    private fun whiteAt(dx: Float): Boolean {
        val img = rule.onNodeWithTag("frame").captureToImage().toPixelMap()
        val px = with(rule.density) { dx.dp.toPx() }
        val c = img[(img.width / 2 + px).toInt(), img.height / 2]
        return c.red > 0.9f && c.green > 0.9f && c.blue > 0.9f
    }

    @Test
    fun doubleTapZoomsInAndBackOut() {
        zoomable()
        assertFalse(whiteAt(35f))
        rule.onNodeWithTag("frame").performTouchInput { doubleClick(center) }
        rule.waitForIdle()
        // 2.5x: the square's half-width grows from 20dp to 50dp.
        assertTrue("double tap should zoom in", whiteAt(35f))
        rule.onNodeWithTag("frame").performTouchInput { doubleClick(center) }
        rule.waitForIdle()
        assertFalse("second double tap should zoom back out", whiteAt(35f))
    }

    @Test
    fun pinchZoomsWithinLimits() {
        zoomable()
        assertFalse(whiteAt(28f))
        rule.onNodeWithTag("frame").performTouchInput {
            pinch(center - Offset(20f, 0f), center - Offset(200f, 0f), center + Offset(20f, 0f), center + Offset(200f, 0f))
        }
        rule.waitForIdle()
        assertTrue("pinch out should zoom in", whiteAt(28f))
        // Pinching in past 1x settles at 1x: the square is back to its own size, never smaller.
        rule.onNodeWithTag("frame").performTouchInput {
            pinch(center - Offset(300f, 0f), center - Offset(5f, 0f), center + Offset(300f, 0f), center + Offset(5f, 0f))
        }
        rule.waitForIdle()
        assertFalse(whiteAt(28f))
        assertTrue("never smaller than 1x", whiteAt(17f))
    }

    @Test
    fun longContentFoldsAndUnfolds() {
        rule.setContent {
            Box(Modifier.testTag("wrap")) {
                Collapsible(key = "t", collapsedHeight = 200.dp, fadeColor = Color.Black) {
                    Box(Modifier.fillMaxWidth().height(900.dp))
                }
            }
        }
        fun height() = rule.onNodeWithTag("wrap").getBoundsInRoot().height.value
        rule.onNodeWithText(str(R.string.show_more)).assertExists()
        assertTrue("folded: ${height()}", height() < 320f)
        rule.onNodeWithText(str(R.string.show_more)).performClick()
        rule.waitForIdle()
        assertTrue("unfolded: ${height()}", height() >= 900f)
        rule.onNodeWithText(str(R.string.show_less)).assertExists().performClick()
        rule.waitForIdle()
        rule.onNodeWithText(str(R.string.show_more)).assertExists()
        assertTrue("folded again: ${height()}", height() < 320f)
    }

    @Test
    fun shortContentHasNoToggle() {
        rule.setContent {
            Collapsible(key = "s", collapsedHeight = 200.dp, fadeColor = Color.Black) {
                Text("A short message")
            }
        }
        rule.onNodeWithText("A short message").assertExists()
        rule.onNodeWithText(str(R.string.show_more)).assertDoesNotExist()
    }
}
