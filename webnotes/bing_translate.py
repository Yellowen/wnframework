#!/bin/python

"""
A layer\interface to Microsoft Translator API
"""


def translate_word(word, from_, to):
    import json
    import requests
    import urllib
    import conf

    args = {
        'client_id': conf.bing_translator_api["client_id"],
        'client_secret': conf.bing_translator_api["client_secret"],
        'scope': 'http://api.microsofttranslator.com',
        'grant_type': 'client_credentials'
    }
    oauth_url = 'https://datamarket.accesscontrol.windows.net/v2/OAuth2-13'
    oauth_junk = json.loads(requests.post(oauth_url,
                                          data=urllib.urlencode(args)).content)

    translation_args = {
        'text': word,
        'to': to,
        'from': from_
    }

    headers = {'Authorization': 'Bearer ' + oauth_junk['access_token']}
    translation_url = 'http://api.microsofttranslator.com/V2/Ajax.svc/Translate?'
    translation_result = requests.get(
        translation_url + urllib.urlencode(translation_args),
        headers=headers)

    return translation_result.content
