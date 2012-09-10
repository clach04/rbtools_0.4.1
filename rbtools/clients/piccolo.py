import os
import re
import sys
import logging

from rbtools.api.errors import APIError
from rbtools.clients import SCMClient, RepositoryInfo
from rbtools.utils.checks import check_gnu_diff, check_install
from rbtools.utils.filesystem import walk_parents
from rbtools.utils.process import execute


# Debugging.  For development...
DEBUG           = False
#DEBUG           = True  # required as --debug flag doesn't log everything, somethings occur before arg parsing

#DEBUG = True  ## FIXME debug remove!
def my_setup_debug():
    if DEBUG:
        LOG_FILENAME = '/tmp/logging_example.out'
        #logging.basicConfig(level=logging.DEBUG)
        #logging.basicConfig()
        #logging.basicConfig(filename=LOG_FILENAME, format='%(asctime)s %(levelname)s %(message)s', level=logging.DEBUG,)
        logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s', level=logging.DEBUG,)
    else:
        logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO,)
my_setup_debug()


class PiccoloClient(SCMClient):
    """A wrapper around the p/p2 Piccolo tool that fetches repository information
    and generates compatible diffs.
    
    Recommended piccolo client version is 2.2.9.
    
    Set environment variables:
        DISABLE_POSTREVIEWPICCOLOCLIENT - to disable piccolo support in postreview
        ENABLE_POSTREVIEWPICCOLOCLIENT  - to force the use of the piccolo command line

    
    NOTE does not yet handle branches! Integrates are sort of handled based on "p working" output used as input to rcompare - review/change looks like a regulr change though.
    
    TODO set REVIEWBOARD_URL/config['REVIEWBOARD_URL'] if not set?
    TODO guess summary/description based on first set of comments?
    TODO for existing changenumber fill in bug number(s)
    will set self.options.username/self.options.submit_as if not set, based on USER operating system variable (required by piccolo, so probably already set)
    
        "%APPDATA%"\.post-review-cookies.txt
    """
    
    def __init__(self, **kwargs):
        super(PiccoloClient, self).__init__(**kwargs)
        # FIXME debug hacks:
        self.options.p2_binary = None
        self.options.p2changenumber = None
        self.options.p2_server = None
        self.options.piccolo_flist = None  # []
    
    def get_repository_info(self):
        my_setup_debug()
        self.p_actualver = os.environ.get('FORCE_PICCOLO_VERSION')
        self.p_minver = (2, 2, 9)
        self.p_minver = (2, 3, 5)  # adds the '-i' flag to rcompare integrated files as needed for review.
        self.p_minver = list(self.p_minver)
        self.p_minver_str = '.'.join(map(str,self.p_minver))
        self.p_bin = self.options.p2_binary or 'p'
        """
        # self.options.debug not populated yet
        if self.options.debug:
            global DEBUG
            DEBUG = True
            .....
        """
        
        if os.environ.get('DISABLE_POSTREVIEWPICCOLOCLIENT'):
            # User requested Piccolo support in postreview be disabled
            logging.debug("piccolo explictly disabled")
            return None
        if self.p_actualver:
            self.p_actualver = map(int, self.p_actualver.split('.'))
        if self.options.p2changenumber or os.environ.get('ENABLE_POSTREVIEWPICCOLOCLIENT'):
            # User requested Piccolo support in postreview be enabled without check
            perform_piccolo_check=False
            logging.debug("not going to perform piccolo check")
        else:
            logging.debug('diff_filename %r', self.options.diff_filename)
            if self.options.diff_filename:
                perform_piccolo_check=False
            else:
                perform_piccolo_check=True
            
        try:
            # Jython only test, consider using a robust check platform module, http://downloads.egenix.com/python/platform.py I think there are others
            os_plat=os.get_os_type()
        except AttributeError:
            os_plat=''
        if sys.platform.startswith('win') or os_plat == 'nt':
            self._command_args = ['cmd', '/C']
        else:
            # probably Unix like...
            self._command_args = ['sh', '-c']

        logging.debug("piccolo bin %r" % self.p_bin)
        if perform_piccolo_check:
            logging.debug("about to check for piccolo")
            if not check_install('%s help' % self.p_bin): # or "p here"? ideally 'p version -c' and then report issues with version (-c option was added to version 2.2.0 of piccolo; p2main.c -> 66 Change 2041 -> 66 (change) on 14-oct-2008 by whiro01)
                # "p version -c" does not require current directory to be in MAPPATH (does not even need MAPPATH set)
                # p help needs mappath (and connection to server)
                logging.debug("piccolo check check_install() failed")
                return None
            # so we have a piccolo command in the path
            if not self.p_actualver:
                # check version of piccolo client .........
                pic_command_str = '%s version -c'  % self.p_bin
                pver_text = execute(self._command_args + [pic_command_str], ignore_errors=True, extra_ignore_errors=(1,))
                logging.info('pver_text %r', pver_text)
                if pver_text.startswith('Invalid option:'):
                    logging.debug("piccolo version check returned Invalid option")
                    # too old, does not support -c
                    print ''
                    print 'Piccolo version too old, (version -c support missing). Need (at least) version %s' % self.p_minver_str
                    return None
                # extract version
                pver_text = pver_text.strip()
                pver_text = pver_text.rsplit(' ', 1)[1]
                pver = pver_text.rsplit('.')
                logging.debug("pver %r" % pver)
                
                #pver = map(int, pver)  # fails if ther are non-integers :-( E.g. 'Piccolo client version 2.2.0b14'
                comparable_pver = []
                for tmp_ver in pver:
                    try:
                        tmp_ver = int(tmp_ver)
                    except ValueError:
                        # probably not an integer, or may be a mix :-(
                        new_tmp_ver = ['0']
                        for tmp_ver_piece in tmp_ver:
                            if tmp_ver_piece in string.digits:
                                new_tmp_ver.append(tmp_ver_piece)
                            else:
                                break
                        tmp_ver = int(''.join(new_tmp_ver))
                    comparable_pver.append(tmp_ver)
                
                self.p_actualver = comparable_pver
                logging.debug("self.p_actualver %r" % self.p_actualver)
                logging.debug("self.p_minver %r" % self.p_minver)
                if self.p_actualver < self.p_minver:
                    print ''
                    print 'Piccolo version too old. Found version %s need version %s' % (pver_text, self.p_minver_str)
                    return None
            
            pic_command_str = '%s here' % self.p_bin
            self._p_here_txt = execute(self._command_args + [pic_command_str], ignore_errors=True, extra_ignore_errors=(1,))
            self._p_here_txt = self._p_here_txt.strip()
            
            # FIXME look at check_gnu_diff() - don't actually need gnu diff under most unix systems BUT do under Windows (mostly likely place for a bad diff exe)
            if sys.platform.startswith('win') or os_plat == 'nt':
                check_gnu_diff()
        else:
            self._p_here_txt = 'EDITME_P2_CLIENT_INFO'  ## TODO do at least minimum hostname and pwd?
        logging.debug('self._p_here_txt %r', self._p_here_txt)
        
        if self.options.submit_as is None:
            self.options.submit_as = os.environ.get('USER')
            if self.options.submit_as and self.options.submit_as.lower() == 'ingres':
                self.options.submit_as = None
        
        #if self.options.username is None:
        #    self.options.username = os.environ.get('USER')
        
        # Ingres Corp only has 1 repository (although there are gateways)
        """
        The Piccolo server (or path) can be obtained with NEWer clients in a very easy fashion:
        
        version 2.2.0 has a neat option:
        
            p map -x
        
        version 2.1.24 does NOT support -x (which restricts to things you have mapped, i.e. can limit the connects) but does support map:
        
            p map
        
        NOTE check version requires Rogers changes.
        
        Can then grep for connect, etc.
        """
        default_piccolo_server = 'usilsuxx:1666'  # and/or pick up from "p map" filtering for connect(s) (shell approach would be; p map |grep '^connect' | awk '{print $4}') if perform_piccolo_check is True
        repository_path = self.options.p2_server or default_piccolo_server # Should try and pick this up from client map, really need a new piccolo command list (first) piccolo server
        
        if self.options.server is None:
            self.options.server = 'http://reviewboard.ingres.prv'  # should consider overridding _get_server_from_config()
        
        return RepositoryInfo(path=repository_path, supports_changesets=False)
        
    def _p_rcompare_diff(self, files):
        """Performs a diff across all modified files in a Piccolo client repository
        or only a diff against specified files.
        
        TODO add check for piccolo version and warn user if old
        NOTE recommended minimum version of piccolo client is 2.2.9; for auto delete file on "reserve -d " improvement
        NOTE recommended minimum version of piccolo client is 2.2.4; massive performance benefits under Windows with this release
        NOTE recommended absolute minimum version of piccolo client is 2.2.1alpha; (for binary and deleted file improvements on diffs/rcompare)
        NOTE recommended minimum version of piccolo client is 2.2.0; for -c option to "p version"
        """
        logging.debug('CMC files %r', files)
        logging.debug('CMC self.options.piccolo_flist %r', self.options.piccolo_flist)
        logging.debug('CMC self.options.diff_filename %r', self.options.diff_filename)
        if self.options.diff_filename:
            """Example:
            
            Step 1 - get diff:
                ## cd $ING_SRC
                ## cd %ING_SRC%
                ## NOTE -i  flag requires piccolo 2.3.5
                p working | p rcompare -i -l - > example_pic.diff
                
            Step 2 - post review
                jython post-review  --p2-diff-filename example_pic.diff --server=http://reviewboard.ingres.prv

                post-review  --server=http://reviewboard.ingres.prv --summary="This is a post-review test by hanal04" --description="Checking current automatic field entry from the command line." --bugs-closed="123456, 98734" --target-groups="admin grp" --target-people="clach04" --submit-as="hanal04 -r 999999"
            """
            diffbytes=open(self.options.diff_filename, 'r').read() ## TODO consider strings instead of bytes? NOTE not using binary as we want to avoid \r values.... This may need further work, this is mostly for win32
            diff_text=diffbytes
        else:
            if self.options.piccolo_flist:
                if self.options.piccolo_flist.strip() == '-':
                    print 'WARNING piccolo - param to -l not supported (yet?), ignoring and assuming all (working) files'
                    self.options.piccolo_flist = None
            
            # Naive "check all working files for integration"
            # Ideally would use file list but wneed errors if files are specified
            pic_command_str = '%s wneed' % (self.p_bin,)
            integration_text = execute(self._command_args + [pic_command_str], extra_ignore_errors=(1,))
            if integration_text:
                warn_text = '''
WARNING opened files are not at headrevs, integration needed before submission.
NOTE this check is for all open files not those specified for review.

These files need integrating:

%s''' % integration_text
                print warn_text
                if not self.options.p2_ignore_wneed:
                    die('Review left unmodified, that is; diffs not uploaded to server.\nThis error can be ignored by specifying the "--p2-ignore-wneed" flag.')
            
            ########### end integration check
            
            # Set piccolo command line command
            # TODO do we need to redirect and capture stderr? "2>&1".
            if self.options.piccolo_flist:
                self.options.piccolo_flist = os.path.abspath(self.options.piccolo_flist)
                working_params = '-l %s ' % self.options.piccolo_flist # TODO do we need to escape the filepath?
            else:
                if files:
                    # Just the names specified on command line (and in current directory as Piccolo paths do not match native paths)
                    working_params = ' '.join(files)
                else:
                    # Any open/reserved file will be diff'd
                    working_params = ' '
            
            logging.debug("pre rcompare; self.p_actualver %r" % self.p_actualver)
            #import pdb ; pdb.set_trace()
            if self.p_actualver < [2, 3, 5]:
                pflag_sane_integration_diffs = ''
            else:
                pflag_sane_integration_diffs = '-i'
            # use -s flag for server side diffs to ensure consistent "\ No newline at end of file" output (e.g. like gnu diff) if newlines are missing at EOF. NOTE server side diffs fail for new reserved files :-(
            pic_command_str = '%s working %s | %s rcompare %s -s -l -' % (self.p_bin, working_params, self.p_bin, pflag_sane_integration_diffs)  # -s for consistent server side diffs, but.....
            pic_command_str = '%s working %s | %s rcompare %s -l -' % (self.p_bin, working_params, self.p_bin, pflag_sane_integration_diffs)  # remove "-s", DEBUG TEST. -s flag to rcompare freaks piccolo out if file is being added
            # be nice if piccolo rcompare supported a new param -working (or similar)
            
            diff_text=execute(self._command_args + [pic_command_str], extra_ignore_errors=(1,))
            # Could add extra sanity check; for decent looking output, e.g. starts with '==='
        return (diff_text, None)
    
    def _p_describe_diff(self, files):
        """Extracts diff from existing (already submitted) piccolo change"""
        """A wrapper around the p/p2 Piccolo tool that ONLY submits reviews of existing changes
        This could be made part of PiccoloChangeClient() but this is at the moment only for testing
        (i..e use existing changes for demo/test data).
        
        Suggested usage:
        
        Unix
            env DISABLE_POSTREVIEWPICCOLOCLIENT=true python /export/home/ingres/clach04/scripts/rb_post.py --server=http://clach04-745.ingres.prv:8000 -c 493916

        
        TODO merge into PiccoloClient (i.e. remove PiccoloChangeClient) so that if -c flag is present it does changes
        """
        try:
            #raise ImportError
            import pypiccolo
        except ImportError:
            pypiccolo = None

        if not self.options.p2changenumber:
            raise APIError('piccolo changenumber missing on command line')
        
        if pypiccolo:
            
            try:
                #raise ImportError
                import cStringIO as StringIO
            except ImportError:
                import StringIO
            
            changenum = self.options.p2changenumber
            change_style = 'full'
            piccolo_lib = pypiccolo.guess_piccolo_lib()
            
            """
            debug_file = open('/tmp/change_full.txt', 'r')
            change_text = debug_file.read()
            debug_file.close()
            """
            p = pypiccolo.Piccolo()
            piccolo_file_obj = StringIO.StringIO()
            return_code = p.describe(changenum, change_style=change_style, piccolo_lib=piccolo_lib, fileptr=piccolo_file_obj)
            change_text = piccolo_file_obj.getvalue()
            piccolo_file_obj.close()
        else:
            change_text = execute([self.p_bin, 'describe', '-s', 'full', self.options.p2changenumber])
        #FIXME parse and then transform the diff
        ################ DEBUG
        #debug_file = open('/tmp/change_full.txt', 'w')
        #debug_file.write(change_text)
        #debug_file.close()
        ################ DEBUG
        change_text = change_text.split('\n')
        
        def piccolo_find_section_start(startcount, expected_marker, change_text):
            """
            startcount integer starting point
            expected_marker string expected start text
            change_text = list of lines
            
            returns line startnumber
            """
            linecount = startcount
            line = ''
            while line != expected_marker:
                linecount += 1
                line = change_text[linecount]
            return linecount        
        expected_marker = '- description -'
        description_start_line = piccolo_find_section_start(3, expected_marker, change_text)
        expected_marker = '- differences -'
        diff_start_line = piccolo_find_section_start(description_start_line, expected_marker, change_text)

        # Only overide if not specifed on command line? TODO decided if we always clobber!
        if not self.options.summary:
            self.options.summary = change_text[3] # 2nd line from of p describe -s descript 493916, etc
            # clean leading chars 
            self.options.summary = self.options.summary[len('   V  '):]
        if not self.options.description:
            ## TODO release notes!! - they currently get dumped to the end, start would be better
            p2_existing_change_warning_line = '-' * 65 + '\n\n'
            p2_existing_change_warning = 'WARNING files that were ADDED have been stripped out\n\n'
            
            self.options.description = '\n'.join(change_text[description_start_line+2:diff_start_line-1])  # output from p describe -s descript 493916 + p describe -s relnotes 493916
            self.options.description = p2_existing_change_warning + p2_existing_change_warning_line + self.options.description + '\n' + p2_existing_change_warning_line + p2_existing_change_warning
        
        difftextlist = []
        file_addition = False
        skip_file_additions = False
        #skip_file_additions = True  # FIXME debug it does work, just not ready for prime time yet
        for line in change_text[diff_start_line+2:]:
            if line:
                if file_addition and skip_file_additions:
                    # really dumb "is this a new file header" check,
                    # not safe if file has a line that starts with 'ingres!'
                    if line.startswith('ingres!'):
                        file_addition = False
                    else:
                        # chomp and throw away
                        continue
                if line.startswith('>') or line.startswith('<') or line.startswith('---') or line[0] in string.digits:
                    difftextlist.append(line)
                else:
                    # Assume we have a piccolo tree + filename + revision
                    # what about branches?                     raise APIError('PiccoloChangeClient.diff unexpected diff context')
                    try:
                        pictree, picfilename, dummy, picrev = line.split()  # for changes
                        logging.debug('in try %r', (pictree, picfilename, int(picrev)))
                        file_addition = False
                    except ValueError:
                        # crappy file name extraction
                        pictree, picfilename, dummy1, dummy2, dummy3, picrev = line.split()  # for file additions
                        picrev = picrev[:-1] #  lose trailing period
                        logging.debug('in except %r', (pictree, picfilename, int(picrev)))
                        file_addition = True
                    if file_addition:
                        #import pdb ; pdb.set_trace()
                        if skip_file_additions:
                            print 'WARNING ignoring ADD file: %r' % line  # FIXME use log.info()
                        else:
                            die("ERROR; Change has a file addition, extracting file addition diffs not implemented. Line\n %r" % line.split())

                    assert '!' in pictree
                    if not file_addition:
                        difftextlist.append('=== %s %s rev %d ====' % (pictree, picfilename, int(picrev)-1))
        
        if skip_file_additions:
            # DEBUG reset
            if file_addition:
                file_addition = False
        
        if file_addition:
            diff_header = '0a%d,%d\n> ' % (1, len(difftextlist))
            difftext = '\n> '.join(difftextlist)
        else:
            print 'add tail'
            difftext = '\n'.join(difftextlist)
        
        return (difftext, None)
    
    def diff(self, files):
        if not self.options.p2changenumber:
            # Normal compare and diff
            return self._p_rcompare_diff(files)
        else:
            # existing change, either test data or for seeing change in context (not actually going to be reviewed)
            return self._p_describe_diff(files)
    
    def guess_group(self, diff_str):
        """naive guess IP group based on piccolo branch name/tree
        Either checks all (default) or uses the path of (only) the first file in the diff
        """
        rawstr = r"""^=== (\S*) (\S*) rev (\d+) ====$"""
        compile_obj = re.compile(rawstr,  re.MULTILINE)
        STOP_ON_FIRST=True
        STOP_ON_FIRST=False
        mailgroups={}
        for ppath, pfilename, prev in compile_obj.findall(diff_str):
            if '!gateway!' in ppath:
                mailgroups['ea'] = None
            elif ppath.startswith('ingtest!gwts1000'):
                mailgroups['ea'] = None
            else:
                first_two_dirs=ppath.split('!', 2)[:2]
                if first_two_dirs[0] == 'ingres':
                    mailgroups[first_two_dirs[1]] = None
            if STOP_ON_FIRST:
                break
        
        mailgroups=list(mailgroups.keys())
        mailgroups.sort()
        result = ','.join(mailgroups)
        return result
        
    def guess_branch(self, diff_str):
        """naive guess piccolo branch name
        Uses the path of (only) the first file in the diff, and uses the first 2 directories
        """
        tmp_line=diff_str.split(' ', 2)[1] # extract path of first file from piccolo diff header
        first_two_dirs=tmp_line.split('!', 2)[:2]
        if first_two_dirs[0] == 'ingres':
            return first_two_dirs[1]
        else:
            return '!'.join(first_two_dirs)
        
    def guess_bugs(self, diff_str):
        """naive guess piccolo bug(s)
        Uses the bug or sirs found in the (in the additions) diff text.
        Can either use first found or all (default)
        Looks for bug or sir numbers on NEW (diff) lines, e.g.:
        
            > bug 123456    - MATCH
            > bug123456     - MATCH
            > b123456       - MATCH
            < bug 356789    - do NOT match
            >     /* see CVLower above, Bug 108802 (move!) */ - MATCH
            > **  18-Jan-2011 (clach04)
            > **      Bug 124933, NULL dereference in DAfre_buffer()- MATCH
            > **      Implemented NUL sanity check in DAfre_buffer()
            > **      (copied from Oracle gateway).

        """
        rawstr = r"""^>.*(?P<bug_or_sir>(?:SIR\s*|BUG\s*|b))(?P<bug_or_sir_num>\d*)\W"""
        compile_obj = re.compile(rawstr, re.IGNORECASE| re.MULTILINE)
        STOP_ON_FIRST=True
        STOP_ON_FIRST=False
        bugs_and_sirs={}
        for change_type, bnum in compile_obj.findall(diff_str):
            #change_type = change_type.upper()
            #if change_type == 'B':
            #    change_type = 'BUG'
            try:
                bnum = str(int(bnum))
            except ValueError:
                # that was not an integer!
                continue
            bugs_and_sirs[bnum] = None
            if STOP_ON_FIRST:
                break
        
        bugs_and_sirs_list=list(bugs_and_sirs.keys())
        bugs_and_sirs_list.sort()
        result = ','.join(bugs_and_sirs_list)
        logging.debug("guess bugs: %r" % result)
        return result
    
    def add_options(self, parser):
        """
        Adds options to an OptionParser.
        NOT used in RBTool - artifact from older version :-( Here as a yet-another reminder
        """
        ## TODO move this into base class and offer both file passing and reading the contents and passing into diff()
        ## see http://groups.google.com/group/reviewboard/browse_thread/thread/2c6b6ee44754b6d9
        ## this way we know the -l flag will not be used in the future for other options! ;-)
        parser.add_option("-l", "--filelist_filename",
                          dest="piccolo_flist", default=None,
                          help='file containing list of files in change, e.g. "p working | grep gwpr > sc"')
        
        parser.add_option("-c", "--changenumber",
                          dest="changenumber", default=None,
                          help='Piccolo (existing) change number')
