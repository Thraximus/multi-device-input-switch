package online.thrax.switcher.ui

import android.content.Context
import android.text.InputType
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager

/** Where typing goes: text, or a named key ("enter", "backspace", "left", …; see hub/touchpad.py). */
interface KeySink {
    fun text(text: String)
    fun key(name: String)
}

/**
 * An invisible view that takes the on-screen keyboard's input and passes it on, with no text box in
 * between: nothing is kept here, every keystroke goes straight to the other machine.
 *
 * Keyboards that still compose words (suggestions) are handled by replaying the difference: replacing
 * "helo" with "hello" sends one backspace and "lo".
 */
class KeyCatcher(context: Context, var sink: KeySink) : View(context) {
    private var composing = ""

    init {
        isFocusable = true
        isFocusableInTouchMode = true
    }

    override fun onCheckIsTextEditor() = true

    override fun onCreateInputConnection(info: EditorInfo): InputConnection {
        // A password-like field: keyboards skip suggestions and autocorrect, and send keys as typed.
        info.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD or
            InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        info.imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI or EditorInfo.IME_FLAG_NO_FULLSCREEN or
            EditorInfo.IME_ACTION_NONE
        composing = ""
        return object : BaseInputConnection(this, false) {
            override fun commitText(text: CharSequence, newCursorPosition: Int): Boolean {
                replaceComposing(text.toString())
                composing = ""
                return true
            }

            override fun setComposingText(text: CharSequence, newCursorPosition: Int): Boolean {
                replaceComposing(text.toString())
                return true
            }

            override fun finishComposingText(): Boolean {
                composing = ""
                return true
            }

            override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
                repeat(beforeLength.coerceAtMost(64)) { sink.key("backspace") }
                repeat(afterLength.coerceAtMost(64)) { sink.key("delete") }
                return true
            }

            override fun performEditorAction(actionCode: Int): Boolean {
                sink.key("enter")
                return true
            }

            override fun sendKeyEvent(event: KeyEvent): Boolean {
                if (event.action == KeyEvent.ACTION_DOWN) onKey(event)
                return true
            }
        }
    }

    // Key events from the on-screen keyboard arrive through sendKeyEvent above. onKeyDown is left alone: those
    // are real keyboards (like the hub's own Bluetooth keyboard), which must not be echoed to another machine.

    private fun onKey(event: KeyEvent) {
        val name = KEYS[event.keyCode]
        if (name != null) {
            sink.key(name)
            return
        }
        val ch = event.unicodeChar
        if (ch != 0) sink.text(String(Character.toChars(ch)))
    }

    private fun replaceComposing(next: String) {
        val common = composing.commonPrefixWith(next).length
        repeat(composing.length - common) { sink.key("backspace") }
        if (next.length > common) sink.text(next.substring(common))
        composing = next
    }

    fun showKeyboard() {
        requestFocus()
        context.getSystemService(InputMethodManager::class.java).showSoftInput(this, 0)
    }

    fun hideKeyboard() {
        context.getSystemService(InputMethodManager::class.java).hideSoftInputFromWindow(windowToken, 0)
    }

    companion object {
        private val KEYS = mapOf(
            KeyEvent.KEYCODE_DEL to "backspace", KeyEvent.KEYCODE_FORWARD_DEL to "delete",
            KeyEvent.KEYCODE_ENTER to "enter", KeyEvent.KEYCODE_NUMPAD_ENTER to "enter", KeyEvent.KEYCODE_TAB to "tab",
            KeyEvent.KEYCODE_ESCAPE to "esc", KeyEvent.KEYCODE_DPAD_UP to "up", KeyEvent.KEYCODE_DPAD_DOWN to "down",
            KeyEvent.KEYCODE_DPAD_LEFT to "left", KeyEvent.KEYCODE_DPAD_RIGHT to "right",
            KeyEvent.KEYCODE_MOVE_HOME to "home", KeyEvent.KEYCODE_MOVE_END to "end",
            KeyEvent.KEYCODE_PAGE_UP to "pageup", KeyEvent.KEYCODE_PAGE_DOWN to "pagedown",
        )
    }
}
