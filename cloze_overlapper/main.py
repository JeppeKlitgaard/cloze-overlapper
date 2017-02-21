# -*- coding: utf-8 -*-

"""
This file is part of the Cloze Overlapper add-on for Anki

Copyright: Glutanimate 2016-2017
License: GNU GPL, version 3 or later; http://www.gnu.org/copyleft/gpl.html
"""

import re
from operator import itemgetter
from itertools import groupby
from BeautifulSoup import BeautifulSoup

from aqt import mw
from aqt import editor
from aqt.utils import tooltip, showWarning
from anki.utils import stripHTML
from anki.hooks import addHook

from .consts import *
from .template import addModel
from .config import *

# OPTIONS

ol_cloze_max = 20
ol_cloze_dfltopts = (1,1,0)
ol_cloze_no_context_first = False
ol_cloze_no_context_last = False
ol_cloze_incremental_ends = False

cloze_reg = r"(?s)\[\[oc(\d+)::((.*?)(::(.*?))?)?\]\]"

class OlClozeGenerator(object):
    """Cloze generator"""

    cformat = u"{{c%i::%s}}"

    def __init__(self, config, settings, max_fields):
        self.config = config
        self.settings = settings
        self.max_fields = max_fields
        self.start = None
        self.total = None

    def generate(self, items, original=None, keys=None):
        """Returns an array of lists with overlapping cloze deletions"""
        before, prompt, after = self.settings
        length = len(items)
        if self.config["incrEnds"]:
            self.total = length + prompt - 1
            self.start = 1
        else:
            self.total = length
            self.start = prompt
        if self.total > self.max_fields:
            return False
        fields = []
        for idx in range(self.start, self.total+1):
            snippets = ["..."] * length
            start_c = self.getClozeStart(idx, prompt)
            start_b = self.getBeforeStart(idx, before, start_c)
            end_a = self.getAfterEnd(idx, after)
            if start_b is not None:
                snippets[start_b:start_c] = items[start_b:start_c]
            if end_a is not None:
                snippets[idx:end_a] = items[idx:end_a]
            snippets[start_c:idx] = self.formatCloze(items[start_c:idx], idx-self.start+1)
            field = self.formatSnippets(snippets, original, keys)
            fields.append(field)
        if self.max_fields > self.total: # delete contents of unused fields
            fields = fields + [""] * (self.max_fields - len(fields))
        fullsnippet = self.formatCloze(items, self.max_fields + 1)
        full = self.formatSnippets(fullsnippet, original, keys)
        return fields, full

    def formatCloze(self, items, nr):
        res = []
        for item in items:
            if not hasattr(item, "__iter__"): #iterable
                res.append(self.cformat % (nr, item))
                continue
            res.append([self.cformat % (nr, i) for i in item])
        return res

    def formatSnippets(self, snippets, original, keys):
        if not original:
            return snippets
        res = original
        print snippets
        for nr, phrases in zip(keys, snippets):
            print "phrases", phrases
            if not hasattr(phrases, "__iter__"):
                if phrases == "...":
                    res = res.replace("{{" + nr + "}}", phrases)
                else:
                    res = res.replace("{{" + nr + "}}", phrases, 1)
                continue
            for phrase in phrases:
                res = res.replace("{{" + nr + "}}", phrase, 1)
        return res

    def getClozeStart(self, idx, target):
        """Determine start index of clozed items"""
        if idx < target or idx > self.total:
            return 0
        return idx-target # looking back from current index

    def getBeforeStart(self, idx, target, start_c):
        """Determine start index of preceding context"""
        if (target == 0 or start_c < 1 
          or (target and self.config["ncLast"] and idx == self.total)):
            return None
        if target is None or target > start_c:
            return 0
        return start_c-target

    def getAfterEnd(self, idx, target):
        """Determine ending index of following context"""
        left = self.total - idx
        if (target == 0 or left < 1
          or (target and self.config["ncFirst"] and idx == self.start)):
            return None
        if target is None or target > left:
            return self.total
        return idx+target


def getNoteSettings(field):
    """Return options tuple. Fall back to defaults if necessary."""
    options = field.replace(" ", "").split(",")
    dflts = ol_cloze_dfltopts
    if not field or not options:
        return ol_cloze_dfltopts, True
    opts = []
    for i in options:
        try:
            opts.append(int(i))
        except ValueError:
            opts.append(None)
    length = len(opts)
    if length == 3 and isinstance(opts[1], int):
        return tuple(opts), False
    elif length == 2 and isinstance(opts[0], int):
        return (opts[1], opts[0], opts[1]), False
    elif length == 1 and isinstance(opts[0], int):
        return (dflts[0], opts[0], dflts[2]), False
    return False, False

def isClozed(html):
    return re.findall(cloze_reg, html)

def getClozeItems(matches):
    matches.sort(key=lambda x: int(x[0]))
    groups = groupby(matches, itemgetter(0))
    items = []
    keys = []
    for key, data in groups:
        phrases = tuple(item[1] for item in data)
        keys.append(key)
        if len(phrases) == 1:
            items.append(phrases[0])
        else:
            items.append(phrases)
    return items, keys

def getLineItems(html):
    """Convert original field HTML to plain text and determine markup tags"""
    soup = BeautifulSoup(html)
    text = soup.getText("\n") # will need to be updated for bs4
    if soup.findAll("ol"):
        markup = "ol"
    elif soup.findAll("ul"):
        markup = "ul"
    else:
        markup = "div"
    items = text.splitlines()
    return items, markup

def processField(field, markup):
    """Convert field contents back to HTML"""
    if not markup:
        return field
    if markup == "div":
        tag_start, tag_end = "", ""
        tag_items = "<div>{0}</div>"
    else:
        tag_start = '<{0}>'.format(markup)
        tag_end = '</{0}>'.format(markup)
        tag_items = "<li>{0}</li>"
    lines = "".join(tag_items.format(line) for line in field)
    return tag_start + lines + tag_end

def updateNote(note, fields, full, markup, defaults):
    """Write changes to note"""
    for idx, field in enumerate(fields):
        name = OLC_FLDS["tx"] + str(idx+1)
        if name not in note:
            return name
        note[name] = processField(field, markup)

    note[OLC_FLDS["fl"]] = processField(full, markup)

    if defaults:
        note[OLC_FLDS["st"]] = ",".join(str(i) for i in ol_cloze_dfltopts)

    return None

def getMaxFields(m):
    prefix = OLC_FLDS["tx"]
    fields = [f['name'] for f in m['flds'] if f['name'].startswith(prefix)]
    last = 0
    for f in fields:
        # check for non-continuous cloze fields
        if not f.startswith(prefix):
            continue
        try:
            cur = int(f.replace(prefix, ""))
        except ValueError:
            break
        if cur != last + 1:
            break
        last = cur
    return len(fields), last

def warnUser(reason, text):
    showWarning(("<b>%s Error</b>: " % reason) + text, title="Cloze Overlapper")

def checkModelIntegrity(m):
    fields = [f['name'] for f in m['flds']]
    for fld in OLC_FLDS.values():
        if fld == OLC_FLDS["tx"]:
            continue
        if fld not in fields:
            return False
    return True

def insertOverlappingCloze(self):
    """Main function, called on button press"""
    setupTemplate()

    model = self.note.model()
    if model["name"] != OLC_MODEL:
        tooltip(u"Can only generate overlapping clozes <br> on '%s' note type" % OLC_MODEL)
        return False

    if not checkModelIntegrity(model):
        warnUser("Note Type", "Fields not configured properly.<br>Please make "
            "sure you didn't remove or rename any of the default fields.")
        return False

    self.web.eval("saveField('key');") # save field
    original = self.note[OLC_FLDS["og"]]

    if not original:
        tooltip(u"Please enter some text in the %s field" % OLC_FLDS["og"])
        return False

    cloze_matches = isClozed(original)
    if not cloze_matches:
        items, markup = getLineItems(original)
        formstr = None
        keys = None
    else:
        markup = None
        formstr = re.sub(cloze_reg, "{{\\1}}", original)
        items, keys = getClozeItems(cloze_matches)

    if not items:
        tooltip("Could not find items to cloze.<br>Please check your input.")
        return False
    if len(items) < 3:
        tooltip("Please enter at least three items to cloze.")
        return False

    note_settings = self.note[OLC_FLDS["st"]]
    settings, defaults = getNoteSettings(note_settings)
    expected_nr, actual_nr = getMaxFields(model)

    if not expected_nr or not actual_nr:
        warnUser("Note Type", "Cloze fields not configured properly")
        return False
    elif expected_nr != actual_nr:
        warnUser("Note Type", "Cloze fields are not continuous."
            "<br>(breaking off after %i fields)" % actual_nr)
        return False

    generator = OlClozeGenerator(default_conf, settings, actual_nr)
    fields, full = generator.generate(items, formstr, keys)

    if not fields:
        tooltip("Warning: More clozes than the note type can handle.")
        return False

    missing = updateNote(self.note, fields, full, markup, defaults)

    if missing:
        showWarning(u"Error: '%s' field missing in the note type" % missing,
            title="Cloze Overlapper")

    self.web.eval("saveField('key');") # save current field
    self.loadNote()
    self.web.eval("focusField(%d);" % self.currentField)


def onSetupButtons(self):
    self._addButton("Cloze Overlapper", self.insertOverlappingCloze,
        _("Alt+Shift+C"), "Generate Overlapping Clozes (Alt+Shift+C)", 
        text="[.]]", size=True)

def setupTemplate():
    model = mw.col.models.byName(OLC_MODEL)
    if not model:
        model = addModel(mw.col)

addHook("profileLoaded", setupTemplate)
editor.Editor.insertOverlappingCloze = insertOverlappingCloze
addHook("setupEditorButtons", onSetupButtons)