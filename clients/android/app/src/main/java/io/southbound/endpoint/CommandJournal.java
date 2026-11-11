package io.southbound.endpoint;

import android.content.ContentValues;
import android.content.Context;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.List;

/** Durable deduplication and receipt outbox. Started != completed after a crash. */
final class CommandJournal extends SQLiteOpenHelper {
    CommandJournal(Context context) { super(context.getApplicationContext(), "commands.db", null, 1); }
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE commands (id TEXT PRIMARY KEY, scope TEXT NOT NULL, "
                + "receipt TEXT, sent INTEGER NOT NULL DEFAULT 0, clicked INTEGER NOT NULL DEFAULT 0, "
                + "click_sent INTEGER NOT NULL DEFAULT 0)");
    }
    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        throw new IllegalStateException("Unsupported journal migration");
    }
    synchronized boolean begin(String scope, String id) {
        ContentValues row = new ContentValues(); row.put("id", id); row.put("scope", scope);
        return getWritableDatabase().insertWithOnConflict("commands", null, row,
                SQLiteDatabase.CONFLICT_IGNORE) != -1;
    }
    synchronized JSONObject receipt(String id) throws Exception {
        try (var cursor = getReadableDatabase().rawQuery("SELECT receipt FROM commands WHERE id=?", new String[]{id})) {
            return cursor.moveToFirst() && !cursor.isNull(0) ? new JSONObject(cursor.getString(0)) : null;
        }
    }
    synchronized void finish(String id, JSONObject receipt) {
        ContentValues values = new ContentValues(); values.put("receipt", receipt.toString()); values.put("sent", 0);
        if (getWritableDatabase().update("commands", values, "id=?", new String[]{id}) != 1)
            throw new IllegalStateException("Command journal missing");
    }
    synchronized List<String> pending(String scope, boolean clicks) {
        List<String> result = new ArrayList<>();
        String condition = clicks ? "clicked=1 AND click_sent=0 AND sent=1" : "receipt IS NOT NULL AND sent=0";
        try (var cursor = getReadableDatabase().rawQuery("SELECT id FROM commands WHERE scope=? AND " + condition,
                new String[]{scope})) {
            while (cursor.moveToNext()) result.add(cursor.getString(0));
        }
        return result;
    }
    synchronized void mark(String id, String column) {
        if (!java.util.Arrays.asList("sent", "clicked", "click_sent").contains(column)) throw new IllegalArgumentException();
        ContentValues values = new ContentValues(); values.put(column, 1);
        getWritableDatabase().update("commands", values, "id=?", new String[]{id});
    }
}
