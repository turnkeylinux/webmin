#!/usr/local/bin/perl

#
# Authentic Theme (https://github.com/authentic-theme/authentic-theme)
# Copyright Ilia Rostovtsev <ilia@virtualmin.com>
# Copyright Alexandr Bezenkov (https://github.com/real-gecko/filemin)
# Licensed under MIT (https://github.com/authentic-theme/authentic-theme/blob/master/LICENSE)
#

use strict;

our (%access, %global_access, %in, %text, $cwd, $path);

require($ENV{'THEME_ROOT'}."/extensions/file-manager/file-manager-lib.pl");

if (!$in{'link'}) {
	redirect_local('list.cgi?path='.urlize($path).'&module=filemin'.extra_query());
	return;
	}

my ($host, $port, $page, $ssl) = parse_http_url($in{'link'});
if (!$host) {
	print_error($text{'error_invalid_uri'});
	}
else {
	my $file = $page;
	my $full;
	$file =~ s/^.*\///;
	$file ||= "index.html";
	$full = fm_checked_cwd_path_or_error($file);
	if (-e $full) {
		print_error(
			text('filemanager_rename_exists', $file, $path,
				$text{'theme_xhred_global_file'}).
			    "."
		);
		}
	else {
		my $success;
		my @st = stat($cwd);
		my $download_callback;
		if (defined(&get_download_address_callback)) {
			my $address_checker = get_download_address_callback(
				$global_access{'download_address_mode'},
				$global_access{'download_allowed_addresses'});
			$download_callback = sub {
				if ($_[0] == 7 && defined($_[1]) && $address_checker) {
					my $address_error = &$address_checker(
						$host, [ $_[1] ]);
					print_error(html_escape($address_error))
						if ($address_error);
					}
				};
			}
		if ($ssl == 0 || $ssl == 1) {
			http_download($host, $port, $page, $full, undef,
				$download_callback,
				$ssl, $in{'username'}, $in{'password'});
			}
		else {
			ftp_download($host, $page, $full, undef, $download_callback,
				$in{'username'}, $in{'password'}, $port);
			}
		set_ownership_permissions($st[4], $st[5], undef, $full);
		@st = stat($cwd);
		$success .= text('http_done', nice_size($st[7]),
			"<tt>".html_escape($full)."</tt>");
		redirect_local('list.cgi?path='.
			    urlize($path).
			    '&module=filemin'.
			    '&success='.
			    $success.
			    extra_query());
		}
	}
