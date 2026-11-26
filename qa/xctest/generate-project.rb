require 'xcodeproj'
require 'fileutils'
require 'pathname'

root = File.expand_path(ARGV.fetch(0))
raise 'Missing suite' unless File.file?(File.join(root, 'HarnessMobileUITests.swift'))
project = Xcodeproj::Project.new(File.join(root, 'HarnessQA.xcodeproj'))
host = project.new_target(:application, 'HarnessProbeHost', :ios, '16.0')
suite = project.new_target(:ui_test_bundle, 'HarnessProbeUITests', :ios, '16.0')
suite.add_dependency(host)
[host, suite].each do |target|
  target.build_configurations.each do |configuration|
    b = configuration.build_settings
    b['PRODUCT_BUNDLE_IDENTIFIER'] = 'org.example.conduit-qa.' + target.name.downcase
    b['GENERATE_INFOPLIST_FILE'] = 'YES'
    b['SWIFT_VERSION'] = '5.0'
    b['IPHONEOS_DEPLOYMENT_TARGET'] = '16.0'
    b['TARGETED_DEVICE_FAMILY'] = '1'
    b['CODE_SIGN_STYLE'] = 'Automatic'
    b['ENABLE_USER_SCRIPT_SANDBOXING'] = 'YES'
    b['TEST_TARGET_NAME'] = host.name if target == suite
  end
end
host.add_file_references([project.main_group.new_file('AppDelegate.swift')])
suite.add_file_references([project.main_group.new_file('HarnessMobileUITests.swift')])
suite.resources_build_phase.add_file_reference(project.main_group.new_file('Bootstrap.plist'))
project.root_object.attributes['TargetAttributes'] = {
  suite.uuid => { 'TestTargetID' => host.uuid }
}
project.save
scheme = Xcodeproj::XCScheme.new
scheme.configure_with_targets(host, suite)
scheme.save_as(project.path, 'HarnessQA', true)
