#
# Copyright 2012-2013 Ontology Engineering Group, Universidad Politecnica de Madrid, Spain
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
# @author Ahmad Alobaid
#


import sys
import string
import random
import json
import os
import subprocess

from django.shortcuts import render, render_to_response
from django.http import HttpResponseRedirect
from django.contrib.auth import login as django_login, logout as django_logout
from django.template import RequestContext
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from mongoengine.queryset import DoesNotExist
import requests
from github import Github

import settings
from autoncore import git_magic, add_webhook, ToolUser, webhook_access, update_g, add_collaborator, \
    clone_repo
from autoncore import parse_online_repo_for_ontologies, update_file, remove_webhook, \
    init_g
from autoncore import get_proper_loggedin_scope, get_ontologies_in_online_repo
from models import *
import autoncore
from settings import client_id, client_secret, host
# import Integrator.previsual as previsual

settings.SECRET_KEY = os.environ['SECRET_KEY']


def get_repos_formatted(the_repos):
    return the_repos


#     repos = []
#     for orir in the_repos:
#         r = {}
#         for ke in orir:
#             r[ke]  = orir[ke]
#         tools = r['monitoring'].split(",")
#         monit=""
#         for t in tools:   
#             keyval = t.split("=")
#             if len(keyval) != 2:
#                 break
#             if keyval[1].lower().strip()=='true':
#                 keyval[1]='Yes'
#             else:
#                 keyval[1]='No'
#             print r['url']+" "+keyval[0]+"="+str(keyval[1])
#             r[keyval[0].strip()]=keyval[1]
#             monit+="=".join(keyval) +","
#         r['monitoring'] = monit
#         repos.append(r)
#     return repos


def home(request):
    print '****** Welcome to home page ********'
    print >> sys.stderr, '****** Welcome to the error output ******'
    if 'target_repo' in request.GET:
        print "we are inside"
        target_repo = request.GET['target_repo']
        if target_repo.strip() == "" or len(target_repo.split('/')) != 2:
            return render(request, 'msg.html', {'msg': 'please enter a valid repo'})
        init_g()
        # if not has_access_to_repo(target_repo):# this for the organization
        # return render(request,'msg.html',{'msg': 'repos under organizations are not supported at the moment'})
        if request.user.is_authenticated():
            ouser = OUser.objects.get(username=request.user.username)
            # this is initially to get the access key for the user
            webhook_access_url, state = webhook_access(client_id, host + '/get_access_token', ouser.private)
        else:  # private access in case not logged in
            webhook_access_url, state = webhook_access(client_id, host + '/get_access_token', True)
        request.session['target_repo'] = target_repo
        request.session['state'] = state
        # if '127.0.0.1:8000' not in request.META['HTTP_HOST']:  # Not testing   # or not settings.test_conf['local']:
        if True:
            request.session['access_token_time'] = '1'
            return HttpResponseRedirect(webhook_access_url)
        if request.user.is_authenticated():
            generateforall(target_repo, request.user.email)
    repos = Repo.objects.order_by('-last_used')[:10]
    return render(request, 'home.html', {'repos': repos, 'user': request.user})


def grant_update(request):
    return render_to_response('msg.html', {'msg': 'Magic is done'}, context_instance=RequestContext(request))


def get_access_token(request):
    if 'state' not in request.session or request.GET['state'] != request.session['state']:
        return HttpResponseRedirect('/')
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': request.GET['code'],
        'redirect_uri': host + '/add_hook'
    }
    res = requests.post('https://github.com/login/oauth/access_token', data=data)
    atts = res.text.split('&')
    d = {}
    for att in atts:
        keyv = att.split('=')
        d[keyv[0]] = keyv[1]
    if 'access_token' not in d:
        print 'access_token is not there'
        return HttpResponseRedirect('/')
    access_token = d['access_token']
    request.session['access_token'] = access_token
    update_g(access_token)
    print 'access_token: ' + access_token

    if request.user.is_authenticated() and request.session['access_token_time'] == '1':
        request.session['access_token_time'] = '2'  # so we do not loop
        isprivate = get_proper_loggedin_scope(OUser.objects.get(username=request.user.username),
                                              request.session['target_repo'])
        print 'isprivate is: ' + str(isprivate)
        webhook_access_url, state = webhook_access(client_id, host + '/get_access_token', isprivate)
        request.session['state'] = state
        return HttpResponseRedirect(webhook_access_url)

    rpy_wh = add_webhook(request.session['target_repo'], host + "/add_hook")
    rpy_coll = add_collaborator(request.session['target_repo'], ToolUser)
    error_msg = ""
    if rpy_wh['status'] == False:
        error_msg += str(rpy_wh['error'])
        print 'error adding webhook: ' + error_msg
    if rpy_coll['status'] == False:
        error_msg += str(rpy_coll['error'])
        print 'error adding collaborator: ' + rpy_coll['error']
    else:
        print 'adding collaborator: ' + rpy_coll['msg']
    if error_msg != "":
        if 'Hook already exists on this repository' in error_msg:
            error_msg = 'This repository already watched'
        elif '404' in error_msg:  # so no enough access according to Github troubleshooting guide
            error_msg = """You don\'t have permission to add collaborators and create webhooks to this repo or this
            repo does not exist. Note that if you can fork this repo, you can add it here"""
            return render_to_response('msg.html', {'msg': error_msg}, context_instance=RequestContext(request))
        msg = error_msg
    else:
        msg = 'webhook attached and user added as collaborator'
    target_repo = request.session['target_repo']
    try:
        repo = Repo.objects.get(url=target_repo)
    except Exception as e:
        print str(e)
        repo = Repo()
        repo.url = target_repo
        repo.save()
    if request.user.is_authenticated():
        ouser = OUser.objects.get(email=request.user.email)
        if repo not in ouser.repos:
            ouser.repos.append(repo)
            ouser.save()
            generateforall(repo.url, ouser.email)
    return render_to_response('msg.html', {'msg': msg},
                              context_instance=RequestContext(request))


def get_changed_files_from_payload(payload):
    commits = payload['commits']
    changed_files = []
    for c in commits:
        changed_files += c["added"] + c["modified"]
    return changed_files


@csrf_exempt
def add_hook(request):
    if settings.TEST:
        print 'We are in test mode'
    try:
        s = str(request.POST['payload'])
        j = json.loads(s, strict=False)
        if j["ref"] == "refs/heads/gh-pages":
            return render(request, 'msg.html', {'msg': 'it is gh-pages, so nothing'})
        s = j['repository']['url'] + 'updated files: ' + str(j['head_commit']['modified'])
        cloning_repo = j['repository']['git_url']
        target_repo = j['repository']['full_name']
        user = j['repository']['owner']['email']
        changed_files = get_changed_files_from_payload(j)
        if 'Merge pull request' in j['head_commit']['message'] or 'OnToology Configuration' == j['head_commit'][
            'message']:
            print 'This is a merge request or Configuration push'
            try:
                repo = Repo.objects.get(url=target_repo)
                print 'got the repo'
                repo.last_used = datetime.today()
                repo.save()
                print 'repo saved'
            except DoesNotExist:
                repo = Repo()
                repo.url = target_repo
                repo.save()
            except Exception as e:
                print 'database_exception: ' + str(e)
            msg = 'This indicate that this merge request will be ignored'
            if settings.TEST:
                print msg
                return
            else:
                return render_to_response('msg.html', {'msg': msg}, context_instance=RequestContext(request))
    except:
        msg = 'This request should be a webhook ping'
        if settings.TEST:
            print msg
            return
        else:
            return render_to_response('msg.html', {'msg': msg}, context_instance=RequestContext(request))
    print '##################################################'
    print 'changed_files: ' + str(changed_files)
    # cloning_repo should look like 'git@github.com:AutonUser/target.git'
    tar = cloning_repo.split('/')[-2]
    cloning_repo = cloning_repo.replace(tar, ToolUser)
    cloning_repo = cloning_repo.replace('git://github.com/', 'git@github.com:')
    comm = "python /home/ubuntu/OnToology/OnToology/autoncore.py "
    comm += ' "' + target_repo + '" "' + user + '" "' + cloning_repo + '" '
    for c in changed_files:
        comm += '"' + c + '" '
    if settings.TEST:
        print 'will call git_magic with target=%s, user=%s, cloning_repo=%s, changed_files=%s' % (target_repo, user,
                                                                                                  cloning_repo,
                                                                                                  str(changed_files))
        git_magic(target_repo, user, cloning_repo, changed_files)
        return
    else:
        print 'running autoncore code as: ' + comm
        subprocess.Popen(comm, shell=True)
        return render_to_response('msg.html', {'msg': '' + s}, context_instance=RequestContext(request))


@login_required
def generateforall_view(request):
    if 'repo' not in request.GET:
        return HttpResponseRedirect('/')
    target_repo = request.GET['repo'].strip()
    found = False
    # The below couple of lines are to check that the user currently have permission over the repository
    try:
        ouser = OUser.objects.get(email=request.user.email)
        for r in ouser.repos:
            if r.url == target_repo:
                found = True
                break
    except:
        return render(request, 'msg.html', {'msg': 'Please contact ontoology@delicias.dia.fi.upm.es'})
    if not found:
        return render(request, 'msg.html',
                      {'msg': 'You need to register/watch this repository while you are logged in'})
    generateforall(target_repo, request.user.email)
    return render_to_response('msg.html', {
        'msg': 'Soon you will find generated files included in a pull request in your repository'},
                              context_instance=RequestContext(request))


def generateforall(target_repo, user_email):
    cloning_repo = 'git@github.com:' + target_repo
    tar = cloning_repo.split('/')[-2].split(':')[1]
    cloning_repo = cloning_repo.replace(tar, ToolUser)
    user = user_email
    ontologies = get_ontologies_in_online_repo(target_repo)
    changed_files = ontologies
    comm = "python /home/ubuntu/OnToology/OnToology/autoncore.py "
    comm += ' "' + target_repo + '" "' + user + '" "' + cloning_repo + '" '
    for c in changed_files:
        comm += '"' + c.strip() + '" '
    if settings.TEST:
        print 'will call git_magic with target=%s, user=%s, cloning_repo=%s, changed_files=%s' % \
              (target_repo, user, cloning_repo, str(changed_files))
        git_magic(target_repo, user, cloning_repo, changed_files)
    else:
        print 'running autoncore code as: ' + comm
        subprocess.Popen(comm, shell=True)


def login(request):
    print '******* login *********'
    if 'username' not in request.GET:
        return HttpResponseRedirect('/')
    username = request.GET['username']
    redirect_url = host + '/login_get_access'
    sec = ''.join([random.choice(string.ascii_letters + string.digits) for _ in range(9)])
    request.session['state'] = sec
    scope = get_proper_scope_to_login(username)
    # scope = 'admin:org_hook'
    # scope+=',admin:org,admin:public_key,admin:repo_hook,gist,notifications,delete_repo,repo_deployment,repo,public_repo,user,admin:public_key'
    redirect_url = "https://github.com/login/oauth/authorize?client_id=" + client_id + "&redirect_uri=" + redirect_url + "&scope=" + scope + "&state=" + sec
    return HttpResponseRedirect(redirect_url)


def logout(request):
    print '*** logout ***'
    django_logout(request)
    return HttpResponseRedirect('/')


def login_get_access(request):
    print '*********** login_get_access ************'
    if 'state' not in request.session:
        request.session['state'] = 'blah123'  # 'state'
    if request.GET['state'] != request.session['state']:
        return HttpResponseRedirect('/')
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': request.GET['code'],
        'redirect_uri': host  # host+'/add_hook'
    }
    res = requests.post('https://github.com/login/oauth/access_token', data=data)
    atts = res.text.split('&')
    d = {}
    for att in atts:
        keyv = att.split('=')
        d[keyv[0]] = keyv[1]
    access_token = d['access_token']
    request.session['access_token'] = access_token
    print 'access_token: ' + access_token
    g = Github(access_token)
    email = g.get_user().email
    username = g.get_user().login
    if email == '' or type(email) == type(None):
        return render(request, 'msg.html', {'msg': 'You have to make you email public and try again'})
    request.session['avatar_url'] = g.get_user().avatar_url
    print 'avatar_url: ' + request.session['avatar_url']
    try:
        user = OUser.objects.get(email=email)
        user.username = username
        user.backend = 'mongoengine.django.auth.MongoEngineBackend'
        user.save()
    except:
        try:
            user = OUser.objects.get(username=username)
            user.email = email
            user.backend = 'mongoengine.django.auth.MongoEngineBackend'
            user.save()
        except:
            print '<%s,%s>' % (email, username)
            sys.stdout.flush()
            sys.stderr.flush()
            # The password is never important but we set it here because it is required by User class
            user = OUser.create_user(username=username, password=request.session['state'], email=email)
            user.backend = 'mongoengine.django.auth.MongoEngineBackend'
            user.save()
    django_login(request, user)
    print 'access_token: ' + access_token
    sys.stdout.flush()
    sys.stderr.flush()
    return HttpResponseRedirect('/')


@login_required
def profilebeta(request):
    try:
        pass
    except Exception as e:
        print 'profile preparing log error [normal]: ' + str(e)
    print '************* profile ************'
    print str(datetime.today())
    ouser = OUser.objects.get(email=request.user.email)
    if 'repo' in request.GET:
        repo = request.GET['repo']
        print 'repo :<%s>' % (repo)
        print 'got the repo'
        try:
            print 'trying to validate repo'
            hackatt = True
            for repooo in ouser.repos:
                if repooo.url == repo:
                    hackatt = False
                    break
            if hackatt:  # trying to access a repo that does not belong to the use currently logged in
                return render(request, 'msg.html', {'msg': 'This repo is not added, please do so in the main page'})
            print 'try to get abs folder'
            if type(autoncore.g) == type(None):
                print 'access token is: ' + request.session['access_token']
                update_g(request.session['access_token'])
            ontologies = parse_online_repo_for_ontologies(repo)
            print 'ontologies: ' + str(len(ontologies))
            for o in ontologies:
                for d in o:
                    print d + ': ' + str(o[d])
            print 'testing redirect'
            print 'will return the Json'
            html = render(request, 'profile_sliders.html', {'ontologies': ontologies}).content
            return JsonResponse({'ontologies': ontologies, 'sliderhtml': html})
        except Exception as e:
            print 'exception: ' + str(e)
    print 'testing redirect'
    repos = ouser.repos
    for r in repos:
        try:
            if len(r.url.split('/')) != 2:
                ouser.update(pull__repos=r)
                r.delete()
                ouser.save()
                continue
            r.user = r.url.split('/')[0]
            r.rrepo = r.url.split('/')[1]
        except:
            ouser.update(pull__repos=r)
            ouser.save()
    return render(request, 'profilebeta.html', {'repos': repos})


@login_required
def profile(request):
    try:
        pass
    except Exception as e:
        print 'profile preparing log error [normal]: ' + str(e)
    print '************* profile ************'
    print str(datetime.today())
    ouser = OUser.objects.get(email=request.user.email)
    if 'repo' in request.GET:
        repo = request.GET['repo']
        print 'repo :<%s>' % (repo)
        print 'got the repo'
        try:
            print 'trying to validate repo'
            hackatt = True
            for repooo in ouser.repos:
                if repooo.url == repo:
                    hackatt = False
                    break
            if hackatt:  # trying to access a repo that does not belong to the use currently logged in
                return render(request, 'msg.html', {'msg': 'This repo is not added, please do so in the main page'})
            print 'try to get abs folder'
            if type(autoncore.g) == type(None):
                print 'access token is: ' + request.session['access_token']
                update_g(request.session['access_token'])
            ontologies = parse_online_repo_for_ontologies(repo)
            print 'ontologies: ' + str(len(ontologies))
            for o in ontologies:
                for d in o:
                    print d + ': ' + str(o[d])
            print 'testing redirect'
            print 'will return the Json'
            html = render(request, 'profile_sliders.html', {'ontologies': ontologies}).content
            return JsonResponse({'ontologies': ontologies, 'sliderhtml': html})
        except Exception as e:
            print 'exception: ' + str(e)
    print 'testing redirect'
    repos = ouser.repos
    for r in repos:
        try:
            if len(r.url.split('/')) != 2:
                ouser.update(pull__repos=r)
                r.delete()
                ouser.save()
                continue
            r.user = r.url.split('/')[0]
            r.rrepo = r.url.split('/')[1]
        except:
            ouser.update(pull__repos=r)
            ouser.save()
    return render(request, 'profile.html', {'repos': repos})


def update_conf(request):
    print 'inside update_conf'
    if request.method == "GET":
        return render(request, "msg.html", {"msg": "This method expects POST only"})
    indic = '-ar2dtool'
    data = request.POST
    print 'will go to the loop'
    for key in data:
        print 'inside the loop'
        if indic in key:
            print 'inside the if'
            onto = key[:-len(indic)]
            ar2dtool = data[onto + '-ar2dtool']
            print 'ar2dtool: ' + str(ar2dtool)
            widoco = data[onto + '-widoco']
            print 'widoco: ' + str(widoco)
            oops = data[onto + '-oops']
            print 'oops: ' + str(oops)
            print 'will call get_conf'
            new_conf = get_conf(ar2dtool, widoco, oops)
            print 'will call update_file'
            onto = 'OnToology' + onto + '/OnToology.cfg'
            try:
                update_file(data['repo'], onto, 'OnToology Configuration', new_conf)
            except Exception as e:
                print 'Error in updating the configuration: ' + str(e)
                return JsonResponse(
                    {'status': False, 'error': str(e)})  # return render(request,'msg.html',{'msg': str(e)})
            print 'returned from update_file'
    print 'will return msg html'
    return JsonResponse({'status': True, 'msg': 'successfully'})


def get_conf(ar2dtool, widoco, oops):
    conf = """
[ar2dtool]
enable = %s

[widoco]
enable = %s

[oops]
enable = %s
    """ % (str(ar2dtool), str(widoco), str(oops))
    return conf


@login_required
def delete_repo(request):
    repo = request.GET['repo']
    user = OUser.objects.get(email=request.user.email)
    for r in user.repos:
        if r.url == repo:
            try:
                user.update(pull__repos=r)
                user.save()
                remove_webhook(repo, host + "/add_hook")
                return JsonResponse({'status': True})
            except Exception as e:
                return JsonResponse({'status': False, 'error': str(e)})
    return JsonResponse({'status': False, 'error': 'You should add this repo first'})


@login_required
def previsual_toggle(request):
    user = OUser.objects.get(email=request.user.email)
    target_repo = request.GET['target_repo']
    found = False
    for repo in user.repos:
        if target_repo == repo.url:
            found = True
            target_repo = repo
            break
    if found:
        target_repo.previsual = not target_repo.previsual
        target_repo.save()
    return HttpResponseRedirect('/profile')


@login_required
def renew_previsual(request):
    user = OUser.objects.get(email=request.user.email)
    target_repo = request.GET['target_repo']
    found = False
    repo = None
    for r in user.repos:
        if target_repo == r.url:
            found = True
            repo = r
            break
    if found:
        repo.previsual_page_available = True
        repo.save()
        autoncore.prepare_log(user.email)
        cloning_repo = 'git@github.com:%s.git' % (
            target_repo)  # cloning_repo should look like 'git@github.com:AutonUser/target.git'
        clone_repo(cloning_repo, user.email, dosleep=True)
        repo_dir = os.path.join(autoncore.home, user.email)
        previsual.start_previsual(repo_dir, target_repo)
        return HttpResponseRedirect('/profile')
    return render(request, 'msg.html',
                  {'msg': 'You should add the repo while you are logged in before the revisual renewal'})


def stepbystep(request):
    return render(request, 'stepbystep.html')


def about(request):
    return render(request, 'about.html')


@login_required
def superadmin(request):
    if request.user.email not in ['ahmad88me@gmail.com']:
        return HttpResponseRedirect('/')
    if 'newstatus' in request.POST:
        new_status = request.POST['newstatus']
        for r in Repo.objects.all():
            r.state = new_status
            r.save()
        return render(request, 'superadmin.html', {'msg': 'statuses of all repos are changed to: ' + new_status})

    return render(request, 'superadmin.html')


def get_proper_scope_to_login(username):
    try:  # The user is registered
        ouser = OUser.objects.get(username=username)
        if ouser.private:
            return 'repo'
        return 'public_repo'  # the user is not private and neither the repo
    except:  # new user
        return 'public_repo'
