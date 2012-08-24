# Copyright (c) 2012 Web Notes Technologies Pvt Ltd (http://erpnext.com)
# 
# MIT License (MIT)
# 
# Permission is hereby granted, free of charge, to any person obtaining a 
# copy of this software and associated documentation files (the "Software"), 
# to deal in the Software without restriction, including without limitation 
# the rights to use, copy, modify, merge, publish, distribute, sublicense, 
# and/or sell copies of the Software, and to permit persons to whom the 
# Software is furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in 
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, 
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A 
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT 
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF 
# CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE 
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
# 

from __future__ import unicode_literals
"""
Transactions are defined as collection of classes, a DocList represents collection of Document
objects for a transaction with main and children.

Group actions like save, etc are performed on doclists
"""

import webnotes, json
import webnotes.model
import webnotes.model.doc
import webnotes.model.doclist
import webnotes.utils.cache

from webnotes.utils import cint, cstr, now, comma_and

class DocListController(object):
	"""
	Collection of Documents with one parent and multiple children
	"""
	def __init__(self, doctype=None, name=None):
		if doctype:
			self.load(doctype, name)
		if hasattr(self, "setup"):
			self.setup()
	
	def load(self, doctype, name=None):
		if isinstance(doctype, list):
			self.set_doclist(doctype)
			return
			
		if not name: name = doctype
		
		self.set_doclist(self.load_doclist(doctype, name))
		
	def load_doclist(self, doctype, name):
		return webnotes.model.doclist.load(doctype, name)
	
	def set_doclist(self, doclist):
		if not isinstance(doclist, webnotes.model.doclist.DocList):
			self.doclist = webnotes.model.doclist.objectify(doclist)
		else:
			self.doclist = doclist
		self.doc = self.doclist[0]

	def save(self):
		"""Save the doclist"""
		self.prepare_for_save()
		self.run('validate')
		self.doctype_validate()
		self.save_main()
		self.save_children()
		self.run('on_update')

	def submit(self):
		"""
			Save & Submit - set docstatus = 1, run "on_submit"
		"""
		self.doc.docstatus = 1 # remove this line after form revamp
		
		if self.doc.docstatus != 1:
			webnotes.msgprint("""Cannot Submit if DocStatus is not set to 1""",
				raise_exception=webnotes.DocStatusError)
		self.save()
		self.run('on_submit')

	def cancel(self):
		"""
			Cancel - set docstatus 2, run "on_cancel"
		"""
		self.doc.docstatus = 2 # remove this line after form revamp
		
		if self.doc.docstatus != 2:
			webnotes.msgprint("""Cannot Cancel if DocStatus is not set to 2""",
				raise_exception=webnotes.DocStatusError)
		self.save()
		self.run('on_cancel')

	def update_after_submit(self):
		"""
			Update after submit - some values changed after submit
		"""
		if self.doc.docstatus != 1:
			webnotes.msgprint("Cannot Update if Document is not Submitted", raise_exception=webnotes.DocStatusError)
		self.save()
		self.run('on_update_after_submit')

	def prepare_for_save(self):
		"""Set owner, modified etc before saving"""
		self.check_if_latest()
		self.check_permission()
		self.check_links()
		self.update_metadata()

	def save_main(self):
		"""Save the main doc"""
		self.doc.save(cint(self.doc.get('__islocal')))

	def save_children(self):
		"""Save Children, with the new parent name"""
		child_map = {}
		
		for d in self.doclist[1:]:
			if d.has_key('parentfield'):
				d.parent = self.doc.name # rename if reqd
				d.parenttype = self.doc.doctype

				d.save(new = cint(d.get('__islocal')))
			
			child_map.setdefault(d.doctype, []).append(d.name)
		
		# delete all children in database that are not in the child_map
		self.remove_children(child_map)

	def remove_children(self, child_map):
		"""delete children from database if they do not exist in the doclist"""
		# get all children types
		tablefields = webnotes.model.get_table_fields(self.doc.doctype)
				
		for dt in tablefields:
			cnames = child_map.get(dt['options']) or []
			if cnames:
				webnotes.conn.sql("""delete from `tab%s` where parent=%s
					and parenttype=%s and name not in (%s)""" % \
					(dt['options'], '%s', '%s', ','.join(['%s'] * len(cnames))), 
					tuple([self.doc.name, self.doc.doctype] + cnames))
			else:
				webnotes.conn.sql("""delete from `tab%s` where parent=%s 
					and parenttype=%s""" % (dt['options'], '%s', '%s'),
					(self.doc.name, self.doc.doctype))

	def check_if_latest(self):
		"""Raises exception if the modified time is not the same as in the database"""
		if not (webnotes.model.is_single(self.doc.doctype) or cint(self.doc.get('__islocal'))):
			modified = webnotes.conn.sql("""select modified from `tab%s`
				where name=%s for update""" % (self.doc.doctype, "%s"), self.doc.name or "")
			
			if modified and unicode(modified[0].modified) != unicode(self.doc.modified):
				webnotes.msgprint("""\
				Document has been modified after you have opened it.
				To maintain the integrity of the data, you will not be able to save your changes.
				Please refresh this document.
				FYI: [%s / %s]""" % \
				(modified[0].modified, self.doc.modified), raise_exception=webnotes.IntegrityError)

	def check_permission(self):
		"""Raises exception if permission is not valid"""
		# hail the administrator - nothing can stop you!
		if webnotes.session["user"] == "Administrator":
			return
		
		doctypelist = webnotes.model.get_doctype("DocType", self.doc.doctype)
		if not hasattr(self, "user_roles"):
			self.user_roles = webnotes.user and webnotes.user.get_roles() or ["Guest"]
		if not hasattr(self, "user_defaults"):
			self.user_defaults = webnotes.user and webnotes.user.get_defaults() or {}
			
		has_perm = False
		match = []
		
		# check if permission exists and if there is any match condition
		for perm in doctypelist.get({"doctype": "DocPerm"}):
			if cint(perm.permlevel) == 0 and cint(perm.read) == 1 and perm.role in self.user_roles:
				has_perm = True
				if perm.match and match != -1:
					match.append(perm.match)
				else:
					# this indicates that there exists atleast one permission
					# where match is not specified
					match = -1
		
		# check match conditions
		if has_perm and match and match != -1:
			for match_field in match:
				if self.doc.get(match_field, "no_value") in self.user_defaults.get(match_field, []):
					# field value matches with user's credentials
					has_perm = True
					break
				else:
					# oops, illegal value
					has_perm = False
					webnotes.msgprint("""Value: "%s" is not allowed for field "%s" """ % \
						(self.doc.get(match_field, "no_value"),
						doctypelist.get_field(match_field).label))

		if not has_perm:
			webnotes.msgprint("""Not enough permissions to save %s: "%s" """ % \
				(self.doc.doctype, self.doc.name), raise_exception=webnotes.PermissionError)

	def check_links(self):
		"""Checks integrity of links (throws exception if links are invalid)"""
		from webnotes.model.doctype import get_link_fields
		link_fields = {}
		error_list = []
		for doc in self.doclist:
			for lf in link_fields.setdefault(doc.doctype, get_link_fields(doc.doctype)):
				options = (lf.options or "").split("\n")[0].strip()
				options = options.startswith("link:") and options[5:] or options
				if doc.get(lf.fieldname) and not webnotes.conn.exists(options, doc[lf.fieldname]):
					error_list.append((options, doc[lf.fieldname], lf.label))

		if error_list:
			webnotes.msgprint("""The following values do not exist in the database: %s.
				Please correct these values and try to save again.""" % \
				comma_and(["%s: \"%s\" (specified in field: %s)" % err for err in error_list]),
				raise_exception=webnotes.InvalidLinkError)

	def update_metadata(self):
		"""Update owner, creation, modified_by, modified, docstatus"""
		ts = now()

		for d in self.doclist:
			if self.doc.get('__islocal'):
				d.owner = webnotes.session["user"]
				d.creation = ts

			d.modified_by = webnotes.session["user"]
			d.modified = ts

	def doctype_validate(self):
		"""run DocType Validator"""
		from core.doctype.doctype_validator.doctype_validator import validate
		validate(self)

	def run(self, method, args=None):
		if hasattr(self, method):
			if args:
				getattr(self, method)(args)
			else:
				getattr(self, method)()

		# if possible, deprecate
		trigger(method, self.doclist[0])

	def clear_table(self, table_field):
		self.doclist = filter(lambda d: d.parentfield != table_field, self.doclist)
	
	def add_child(self, doc):
		"""add a child doc to doclist"""
		# make child
		if not isinstance(doc, webnotes.model.doc.Document):
			doc = webnotes.model.doc.Document(fielddata = doc)
		doc.__islocal = 1
		doc.parent = self.doc.name
		doc.parenttype = self.doc.doctype
		# parentfield is to be supplied in the doc
		
		# add to doclist
		self.doclist.append(doc)
	
	# TODO: should this method be here?
	def get_csv_from_attachment(self):
		"""get csv from attachment"""
		if not self.doc.file_list:
		  msgprint("File not attached!")
		  raise Exception

		# get file_id
		fid = self.doc.file_list.split(',')[1]
	  
		# get file from file_manager
		try:
			from webnotes.utils import file_manager
			fn, content = file_manager.get_file(fid)
		except Exception, e:
			webnotes.msgprint("Unable to open attached file. Please try again.")
			raise e

		# convert char to string (?)
		if not isinstance(content, basestring) and hasattr(content, 'tostring'):
		  content = content.tostring()

		import csv
		return csv.reader(content.splitlines())

def trigger(method, doc):
	"""trigger doctype events"""
	try:
		import startup.event_handlers
	except ImportError:
		return
		
	if hasattr(startup.event_handlers, method):
		getattr(startup.event_handlers, method)(doc)
		
	if hasattr(startup.event_handlers, 'doclist_all'):
		startup.event_handlers.doclist_all(doc, method)
